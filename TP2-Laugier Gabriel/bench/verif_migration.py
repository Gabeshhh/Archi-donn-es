"""Mesures 5a et 5b : prouver que la migration PostgreSQL -> MongoDB / Cassandra n'a rien perdu.

5a : même nombre de messages partout, et même nombre PAR SALON (deux erreurs qui se compensent
     ne passent pas inaperçues).
     Cassandra : pas de SELECT count(*) sur toute la table. Sans clé de partition, cette requête
     oblige le coordinateur à lire TOUTES les partitions de TOUS les nœuds : c'est un parcours
     complet du cluster, qui dépasse le délai d'attente dès que la table est grosse (et charge
     tous les nœuds pendant ce temps). On compte donc partition par partition :
     un count(*) DANS une partition (salon, fenêtre) ne lit que cette partition.
5b : dark launch : les 50 mêmes identifiants, dans le même ordre, sur les trois moteurs.

Usage : python verif_migration.py
"""
from cassandra.concurrent import execute_concurrent_with_args

from common import EPOCH_MS, bucket_de, cassandra_session, mongo_db, pg_conn

SALONS_TEST = {"chaud": 775, "moyen": 424, "clairsemé": 342}

pg = pg_conn()
mdb = mongo_db()
cs = cassandra_session("discord")
cs.default_timeout = 60

DERNIER_BUCKET = bucket_de(pg.execute("SELECT max(id) FROM app.message").fetchone()[0])
salons = pg.execute("SELECT id, cree_le FROM app.salon WHERE type = 'texte' ORDER BY id").fetchall()
creation = {s: bucket_de(int(c.timestamp() * 1000 - EPOCH_MS) << 22) for s, c in salons}

# ---------------- 5a : totaux ----------------
total_pg = pg.execute("SELECT count(*) FROM app.message").fetchone()[0]
par_salon_pg = dict(pg.execute("SELECT salon_id, count(*) FROM app.message GROUP BY 1").fetchall())

total_mongo = mdb.messages.count_documents({})
par_salon_mongo = {d["_id"]: d["n"] for d in mdb.messages.aggregate([{"$group": {"_id": "$salon_id", "n": {"$sum": 1}}}])}

compte = cs.prepare("SELECT count(*) FROM messages_par_salon WHERE salon_id = ? AND bucket = ?")
partitions = [(s, b) for s, _ in salons for b in range(creation[s], DERNIER_BUCKET + 1)]
par_salon_cs, partitions_non_vides = {}, 0
for (s, _b), (ok, res) in zip(partitions, execute_concurrent_with_args(cs, compte, partitions, concurrency=64)):
    if not ok:
        raise SystemExit(f"lecture en échec sur le salon {s} : {res}")
    n = res.one()[0]
    if n:
        partitions_non_vides += 1
        par_salon_cs[s] = par_salon_cs.get(s, 0) + n
total_cs = sum(par_salon_cs.values())

print("Mesure 5a")
print("| Moteur | Messages | Salons dont le compte diffère de PostgreSQL |\n|---|---:|---:|")
print(f"| PostgreSQL | {total_pg} | - |")
print(f"| MongoDB | {total_mongo} | {sum(1 for s in par_salon_pg if par_salon_mongo.get(s) != par_salon_pg[s])} |")
print(f"| Cassandra (somme de {len(partitions)} count(*) par partition, {partitions_non_vides} non vides) "
      f"| {total_cs} | {sum(1 for s in par_salon_pg if par_salon_cs.get(s) != par_salon_pg[s])} |")
assert total_pg == total_mongo == total_cs, "les totaux diffèrent"
assert par_salon_pg == par_salon_mongo == par_salon_cs, "un salon au moins a un compte différent"

# Taille de la collection MongoDB et plan de B1 (l'index suffit-il ?)
st = mdb.command("collStats", "messages")
plan = mdb.messages.find({"salon_id": SALONS_TEST["chaud"]}, {"_id": 1}).sort("_id", -1).limit(50).explain()["queryPlanner"]["winningPlan"]
etapes, e = [], plan
while e:
    etapes.append(e["stage"] + (f" ({e['indexName']})" if "indexName" in e else ""))
    e = e.get("inputStage")
print(f"\n# mongo_taille : {st['size'] / 2**20:.0f} Mo de données, {st['totalIndexSize'] / 2**20:.0f} Mo d'index")
print(f"# mongo_plan_b1 : {' <- '.join(etapes)}")

# ---------------- 5b : dark launch ----------------
sel = cs.prepare("SELECT message_id FROM messages_par_salon WHERE salon_id = ? AND bucket = ? LIMIT ?")


def derniers_cassandra(salon):
    ids, b = [], DERNIER_BUCKET
    while b >= creation[salon] and len(ids) < 50:
        ids += [r.message_id for r in cs.execute(sel, (salon, b, 50 - len(ids)))]
        b -= 1
    return ids


print("\nMesure 5b")
print("| Salon | Messages lus | PostgreSQL = MongoDB | PostgreSQL = Cassandra | Premier id | Dernier id |\n|---|---:|:-:|:-:|---|---|")
for nom, s in SALONS_TEST.items():
    a = [x for (x,) in pg.execute("SELECT id FROM app.message WHERE salon_id = %s ORDER BY id DESC LIMIT 50", (s,)).fetchall()]
    b = [d["_id"] for d in mdb.messages.find({"salon_id": s}, {"_id": 1}).sort("_id", -1).limit(50)]
    c = derniers_cassandra(s)
    print(f"| {nom} ({s}) | {len(a)} | {'oui' if a == b else 'NON'} | {'oui' if a == c else 'NON'} | {a[0]} | {a[-1]} |")
    assert a == b == c, f"salon {s} : les listes diffèrent"
print("\nMigration vérifiée : rien de perdu, même ordre partout.")

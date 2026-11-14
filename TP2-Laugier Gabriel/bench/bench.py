"""Benchmark : chaque cas d'usage, chaque moteur qui y prétend, mêmes entrées.

Usage : python bench.py [--n 300]
Affiche un tableau Markdown à coller dans MESURES.md. Les moteurs doivent d'abord donner la MÊME réponse (asserts).
Complété par LAUGIER Gabriel (B3 Dev).
"""
import argparse
import random
import statistics
import time
from datetime import datetime, timezone

from common import bucket_de, cassandra_session, mongo_db, neo_driver, pg_conn, redis_conn

pg, mdb, rds, neo = pg_conn(), mongo_db(), redis_conn(), neo_driver()
cs = cassandra_session("discord")
SELECT_CS = cs.prepare("SELECT message_id FROM messages_par_salon WHERE salon_id = ? AND bucket = ? LIMIT ?")
try:   # table du correctif optionnel (créée par load_nosql.py)
    SELECT_FENETRES = cs.prepare("SELECT bucket FROM fenetres_par_salon WHERE salon_id = ?")
except Exception:
    SELECT_FENETRES = None
NEO = neo.session()

# Salons de test choisis à la mission 0 (un par profil de serveur du dossier documentaire)
SALON_CHAUD, SALON_MOYEN, SALON_CLAIRSEME = 775, 424, 342   # 12 952 / 90 / 1 messages
DERNIER_BUCKET = bucket_de(pg.execute("SELECT max(id) FROM app.message").fetchone()[0])
CREATION = {s: bucket_de(int(c.timestamp() * 1000 - 1420070400000) << 22)
            for s, c in pg.execute("SELECT id, cree_le FROM app.salon").fetchall()}
NB_UTILISATEURS = pg.execute("SELECT max(id) FROM app.utilisateur").fetchone()[0]
SERVEUR_TEST = 118
# Octobre 2026 entier : FIN est EXCLUE, donc 1er novembre (avec le 31 octobre, on perdait le dernier jour)
DEBUT, FIN = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)


# ---------- B1. Les 50 derniers messages d'un salon ----------
def b1_pg(salon):
    return [x[0] for x in pg.execute("SELECT id FROM app.message WHERE salon_id = %s ORDER BY id DESC LIMIT 50", (salon,)).fetchall()]


def b1_mongo(salon):
    # index b1_salon_id (salon_id, _id décroissant) : documents lus déjà dans l'ordre, arrêt à 50
    return [d["_id"] for d in mdb.messages.find({"salon_id": salon}, {"_id": 1}).sort("_id", -1).limit(50)]


def fenetres_lues(salon):
    """Nombre de partitions (salon, fenêtre) que b1_cassandra interroge pour ce salon."""
    n, trouves, b = 0, 0, DERNIER_BUCKET
    while b >= CREATION[salon] and trouves < 50:
        trouves += len(list(cs.execute(SELECT_CS, (salon, b, 50 - trouves))))
        n += 1
        b -= 1
    return n


def b1_cassandra(salon):
    # Une requête par fenêtre de 10 jours, de la plus récente à la plus ancienne,
    # jusqu'à avoir 50 messages ou atteindre la fenêtre de création du salon.
    # Les requêtes sont faites l'une après l'autre : on ne sait pas d'avance combien il en faut.
    ids, b = [], DERNIER_BUCKET
    while b >= CREATION[salon] and len(ids) < 50:
        ids += [r.message_id for r in cs.execute(SELECT_CS, (salon, b, 50 - len(ids)))]
        b -= 1
    return ids


def b1_cassandra_fenetres(salon):
    # Correctif : on lit d'abord la liste des fenêtres NON VIDES du salon (1 petite partition),
    # puis seulement ces fenêtres. Les fenêtres vides ne sont plus jamais interrogées.
    if SELECT_FENETRES is None:
        raise NotImplementedError("table fenetres_par_salon absente : relancer load_nosql.py")
    ids = []
    for r in cs.execute(SELECT_FENETRES, (salon,)):          # déjà triées de la plus récente à la plus ancienne
        ids += [x.message_id for x in cs.execute(SELECT_CS, (salon, r.bucket, 50 - len(ids)))]
        if len(ids) >= 50:
            break
    return ids


# ---------- B2. Présence : battement de cœur (écriture) et « combien sont en ligne dans ce serveur ? » (lecture) ----------
def b2w_pg(uid):
    pg.execute("INSERT INTO app.presence VALUES (%s, now()) ON CONFLICT (utilisateur_id) DO UPDATE SET vu_le = now()", (uid,))


def b2w_redis(uid):
    # la clé disparaît d'elle-même au bout de 300 s sans nouveau battement de cœur (RG12)
    rds.set(f"presence:{uid}", "en_ligne", ex=300)

def b2r_pg(serveur):
    return pg.execute("""SELECT count(*) FROM app.membre m JOIN app.presence p ON p.utilisateur_id = m.utilisateur_id
                         WHERE m.serveur_id = %s AND p.vu_le > now() - interval '5 minutes'""", (serveur,)).fetchone()[0]


def b2r_redis(serveur):
    # 2 allers-retours : la liste des membres (un ensemble), puis toutes leurs présences d'un coup.
    # Une présence expirée n'existe plus : MGET renvoie None pour elle.
    membres = rds.smembers(f"membres:{serveur}")
    if not membres:
        return 0
    return sum(1 for v in rds.mget([f"presence:{m}" for m in membres]) if v is not None)

# ---------- B3. Suggestions : serveurs où au moins 2 de mes amis sont membres et pas moi ----------
def b3_pg(uid):
    return pg.execute("""
        SELECT m.serveur_id, count(*) AS n
        FROM (SELECT utilisateur_b AS ami FROM app.amitie WHERE utilisateur_a = %(u)s
              UNION ALL SELECT utilisateur_a FROM app.amitie WHERE utilisateur_b = %(u)s) f
        JOIN app.membre m ON m.utilisateur_id = f.ami
        WHERE m.serveur_id NOT IN (SELECT serveur_id FROM app.membre WHERE utilisateur_id = %(u)s)
        GROUP BY m.serveur_id HAVING count(*) >= 2 ORDER BY n DESC, m.serveur_id LIMIT 5""", {"u": uid}).fetchall()


def b3_neo(uid):
    rows = NEO.run("""
        MATCH (u:Utilisateur {id: $u})-[:AMI_DE]-(f:Utilisateur)-[:MEMBRE_DE]->(s:Serveur)
        WHERE NOT EXISTS { (u)-[:MEMBRE_DE]->(s) }
        WITH s, count(DISTINCT f) AS n
        WHERE n >= 2
        RETURN s.id AS serveur_id, n
        ORDER BY n DESC, serveur_id LIMIT 5""", u=uid).data()
    return [(r["serveur_id"], r["n"]) for r in rows]

# ---------- B3 bis. Amis d'amis : personnes que je ne connais pas, classées par nombre d'amis communs ----------
def b3bis_pg(uid):
    # L'amitié est stockée une seule fois (a < b) : à chaque saut il faut regarder les deux colonnes.
    # count(*) = nombre d'amis communs (chaque chemin passe par un ami différent).
    return pg.execute("""
        WITH amis AS (
            SELECT utilisateur_b AS ami FROM app.amitie WHERE utilisateur_a = %(u)s
            UNION
            SELECT utilisateur_a FROM app.amitie WHERE utilisateur_b = %(u)s),
        amis_damis AS (
            SELECT x.utilisateur_b AS candidat FROM amis a JOIN app.amitie x ON x.utilisateur_a = a.ami
            UNION ALL
            SELECT x.utilisateur_a FROM amis a JOIN app.amitie x ON x.utilisateur_b = a.ami)
        SELECT candidat, count(*) AS communs
        FROM amis_damis
        WHERE candidat <> %(u)s AND candidat NOT IN (SELECT ami FROM amis)
        GROUP BY candidat
        ORDER BY communs DESC, candidat LIMIT 10""", {"u": uid}).fetchall()


def b3bis_neo(uid):
    rows = NEO.run("""
        MATCH (u:Utilisateur {id: $u})-[:AMI_DE]-(f:Utilisateur)-[:AMI_DE]-(c:Utilisateur)
        WHERE c <> u AND NOT EXISTS { (u)-[:AMI_DE]-(c) }
        RETURN c.id AS candidat, count(DISTINCT f) AS communs
        ORDER BY communs DESC, candidat LIMIT 10""", u=uid).data()
    return [(r["candidat"], r["communs"]) for r in rows]

# ---------- B4. Activité d'un serveur : messages et auteurs distincts par jour, octobre 2026 ----------
def b4_pg_normalise(serveur):
    return pg.execute("""
        SELECT (m.envoye_le AT TIME ZONE 'UTC')::date AS jour, count(*), count(DISTINCT m.auteur_id)
        FROM app.message m JOIN app.salon s ON s.id = m.salon_id
        WHERE s.serveur_id = %s AND m.envoye_le >= %s AND m.envoye_le < %s
        GROUP BY 1 ORDER BY 1""", (serveur, DEBUT, FIN)).fetchall()


def b4_pg_vue(serveur):
    return pg.execute("""
        SELECT jour, nb_messages, nb_auteurs FROM app.activite_serveur_jour
        WHERE serveur_id = %s AND jour >= %s AND jour < %s ORDER BY jour""",
                      (serveur, DEBUT.date(), FIN.date())).fetchall()


def b4_mongo(serveur):
    # index b4_serveur_date (serveur_id, envoye_le) pour le $match ; le $group se fait en mémoire
    res = mdb.messages.aggregate([
        {"$match": {"serveur_id": serveur, "envoye_le": {"$gte": DEBUT, "$lt": FIN}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$envoye_le"}},
                    "nb": {"$sum": 1}, "auteurs": {"$addToSet": "$auteur_id"}}},
        {"$project": {"nb": 1, "nb_auteurs": {"$size": "$auteurs"}}},
        {"$sort": {"_id": 1}},
    ])
    return [(datetime.strptime(d["_id"], "%Y-%m-%d").date(), d["nb"], d["nb_auteurs"]) for d in res]

def chrono(f, entrees, echauffement=10):
    for e in entrees[:echauffement]:
        f(*e)
    t = []
    for e in entrees:
        t0 = time.perf_counter()
        f(*e)
        t.append((time.perf_counter() - t0) * 1000)
    t.sort()
    return statistics.median(t), t[int(len(t) * 0.95) - 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    n = ap.parse_args().n
    rnd = random.Random(1)

    # ---- Contrôles de cohérence AVANT tout chronométrage ----
    # Un moteur qui répond vite une réponse fausse (index manquant, données mal chargées, requête
    # qui oublie un cas) gagnerait le benchmark : on vérifie d'abord que tous disent la même chose.
    for salon in (SALON_CHAUD, SALON_MOYEN, SALON_CLAIRSEME):
        ref = b1_pg(salon)
        assert ref == b1_mongo(salon), f"B1 salon {salon} : MongoDB différent de PostgreSQL"
        assert ref == b1_cassandra(salon), f"B1 salon {salon} : Cassandra différent de PostgreSQL"
        if SELECT_FENETRES is not None:
            assert ref == b1_cassandra_fenetres(salon), f"B1 salon {salon} : correctif différent"
        print(f"# B1 salon {salon} : {len(ref)} messages identiques, {fenetres_lues(salon)} fenêtre(s) Cassandra lue(s)")
    controle = random.Random(99)   # tirage à part : ne change pas les entrées chronométrées plus bas
    # B2 : on rafraîchit les MÊMES présences des deux côtés, sinon on compare des états différents
    for u in [controle.randint(1, NB_UTILISATEURS) for _ in range(500)]:
        b2w_pg(u)
        b2w_redis(u)
    for serveur in (SERVEUR_TEST, 20, 65, 52):
        assert b2r_pg(serveur) == b2r_redis(serveur), f"B2b serveur {serveur} : Redis différent"
    print(f"# B2b serveur {SERVEUR_TEST} : {b2r_pg(SERVEUR_TEST)} membres en ligne des deux côtés")
    for u in [controle.randint(1, NB_UTILISATEURS) for _ in range(50)]:
        assert [tuple(r) for r in b3_pg(u)] == b3_neo(u), f"B3 utilisateur {u} : Neo4j différent"
        assert [tuple(r) for r in b3bis_pg(u)] == b3bis_neo(u), f"B3 bis utilisateur {u} : Neo4j différent"
    for serveur in (SERVEUR_TEST, 20, 65):
        ref = [tuple(r) for r in b4_pg_normalise(serveur)]
        assert ref == [tuple(r) for r in b4_pg_vue(serveur)], f"B4 serveur {serveur} : vue différente (REFRESH ?)"
        assert ref == b4_mongo(serveur), f"B4 serveur {serveur} : MongoDB différent"
    print("# contrôles de cohérence : OK")

    lignes = []

    def mesure(cas, moteur, f, entrees):
        try:
            p50, p95 = chrono(f, entrees)
            lignes.append(f"| {cas} | {moteur} | {p50:.2f} | {p95:.2f} |")
        except NotImplementedError:
            lignes.append(f"| {cas} | {moteur} | à faire | à faire |")

    for nom, salon in (("chaud", SALON_CHAUD), ("moyen", SALON_MOYEN), ("clairsemé", SALON_CLAIRSEME)):
        e = [(salon,)] * n
        cas = f"B1 50 derniers messages, salon {nom}"
        mesure(cas, "PostgreSQL", b1_pg, e)
        mesure(cas, "MongoDB", b1_mongo, e)
        mesure(cas, "Cassandra", b1_cassandra, e)
        mesure(cas, "Cassandra + votre correctif (optionnel)", b1_cassandra_fenetres, e)
    e = [(rnd.randint(1, NB_UTILISATEURS),) for _ in range(n)]
    mesure("B2a présence : écriture", "PostgreSQL", b2w_pg, e)
    mesure("B2a présence : écriture", "Redis", b2w_redis, e)
    e = [(SERVEUR_TEST,)] * n
    mesure("B2b présence : en ligne dans un serveur (478 membres)", "PostgreSQL", b2r_pg, e)
    mesure("B2b présence : en ligne dans un serveur (478 membres)", "Redis", b2r_redis, e)
    e = [(rnd.randint(1, NB_UTILISATEURS),) for _ in range(n)]
    mesure("B3 serveurs suggérés (amis)", "PostgreSQL", b3_pg, e)
    mesure("B3 serveurs suggérés (amis)", "Neo4j", b3_neo, e)
    e = [(rnd.randint(1, NB_UTILISATEURS),) for _ in range(30)]
    mesure("B3 bis amis d'amis (n=30)", "PostgreSQL", b3bis_pg, e)
    mesure("B3 bis amis d'amis (n=30)", "Neo4j", b3bis_neo, e)
    e = [(SERVEUR_TEST,)] * n
    mesure("B4 activité d'un serveur (30 j)", "PostgreSQL normalisé", b4_pg_normalise, e)
    mesure("B4 activité d'un serveur (30 j)", "PostgreSQL vue matérialisée", b4_pg_vue, e)
    mesure("B4 activité d'un serveur (30 j)", "MongoDB (aggregate)", b4_mongo, e)

    print(f"| Cas | Moteur | p50 (ms) | p95 (ms) |   (n = {n})\n|---|---|---:|---:|")
    print("\n".join(lignes))


if __name__ == "__main__":
    main()

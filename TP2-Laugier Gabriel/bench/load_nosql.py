"""Alimente MongoDB, Cassandra, Redis et Neo4j à partir de PostgreSQL (source de vérité du TP).

Usage : python load_nosql.py [--sans-cassandra]
"""
import argparse
import json
import random

from cassandra import ConsistencyLevel
from cassandra.concurrent import execute_concurrent_with_args
from cassandra.query import UNSET_VALUE

from common import bucket_de, cassandra_session, mongo_db, neo_driver, pg_conn, redis_conn

ap = argparse.ArgumentParser()
ap.add_argument("--sans-cassandra", action="store_true")
args = ap.parse_args()
pg = pg_conn()


def lots(curseur, taille):
    while True:
        rows = curseur.fetchmany(taille)
        if not rows:
            return
        yield rows


# ---------- MongoDB : la configuration « 2015 » — une collection, un index composé ----------
# Un document par message. Champs embarqués : tout ce que l'écran « 50 derniers messages » affiche
# (sauf le pseudo : il change, on le garde dans PostgreSQL), plus serveur_id pour le tableau de bord (B4).
# La pièce jointe est un SOUS-DOCUMENT du message (sa forme change selon le type : image, fichier, lien),
# présente seulement s'il y en a une. Même chose pour modifie_le. Pas de champ à null.
REQ_MESSAGES = """
    SELECT m.id, m.salon_id, s.serveur_id, m.auteur_id, m.contenu, m.envoye_le, m.modifie_le, m.pieces_jointes
    FROM app.message m JOIN app.salon s ON s.id = m.salon_id
    ORDER BY m.id"""


def lire_messages(taille=10_000):
    """Parcourt les messages de PostgreSQL par paquets (curseur côté serveur : pas tout en mémoire)."""
    with pg.transaction():
        with pg.cursor("export_messages") as c:
            c.execute(REQ_MESSAGES)
            yield from lots(c, taille)


db = mongo_db()
db.messages.drop()
nb_mongo = 0
for paquet in lire_messages():
    docs = []
    for mid, salon, serveur, auteur, contenu, envoye, modifie, pj in paquet:
        d = {"_id": mid, "salon_id": salon, "serveur_id": serveur, "auteur_id": auteur,
             "contenu": contenu, "envoye_le": envoye}
        if modifie is not None:
            d["modifie_le"] = modifie
        if pj is not None:
            d["pieces_jointes"] = pj          # psycopg rend déjà le JSONB sous forme de dict
        docs.append(d)
    db.messages.insert_many(docs, ordered=False)
    nb_mongo += len(docs)
# B1 : derniers messages d'un salon -> égalité sur salon_id, puis tri par _id décroissant.
#      L'index donne les documents déjà dans l'ordre : pas de tri en mémoire, arrêt à 50.
db.messages.create_index([("salon_id", 1), ("_id", -1)], name="b1_salon_id")
# B4 : activité d'un serveur sur une période -> égalité sur serveur_id, puis intervalle sur envoye_le.
db.messages.create_index([("serveur_id", 1), ("envoye_le", 1)], name="b4_serveur_date")
print(f"mongodb   : {nb_mongo} documents insérés, {db.messages.count_documents({})} dans la collection")


# ---------- Cassandra : une table = une requête ----------
# Schéma : voir cql/01_schema.cql (mêmes ordres ci-dessous, pour que ce script se suffise à lui-même).
# Règle apprise à l'article : n'écrivez JAMAIS de NULL (UNSET_VALUE) — un NULL crée une tombstone.
SCHEMA_CQL = [
    """CREATE KEYSPACE IF NOT EXISTS discord
       WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}""",
    """CREATE TABLE IF NOT EXISTS discord.messages_par_salon (
           salon_id bigint, bucket int, message_id bigint,
           auteur_id bigint, contenu text, envoye_le timestamp, modifie_le timestamp, pieces_jointes text,
           PRIMARY KEY ((salon_id, bucket), message_id)
       ) WITH CLUSTERING ORDER BY (message_id DESC)""",
    """CREATE TABLE IF NOT EXISTS discord.fenetres_par_salon (
           salon_id bigint, bucket int,
           PRIMARY KEY (salon_id, bucket)
       ) WITH CLUSTERING ORDER BY (bucket DESC)""",
]
if not args.sans_cassandra:
    cs = cassandra_session()
    cs.default_timeout = 60
    for ordre in SCHEMA_CQL:
        cs.execute(ordre)
    cs.set_keyspace("discord")
    cs.execute("TRUNCATE messages_par_salon")
    cs.execute("TRUNCATE fenetres_par_salon")
    ins = cs.prepare("""INSERT INTO messages_par_salon
                        (salon_id, bucket, message_id, auteur_id, contenu, envoye_le, modifie_le, pieces_jointes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""")
    ins.consistency_level = ConsistencyLevel.ONE
    ins_fenetre = cs.prepare("INSERT INTO fenetres_par_salon (salon_id, bucket) VALUES (?, ?)")
    nb_ok = nb_echec = 0
    fenetres = set()
    for paquet in lire_messages(5_000):
        lignes = []
        for mid, salon, _serveur, auteur, contenu, envoye, modifie, pj in paquet:
            b = bucket_de(mid)
            fenetres.add((salon, b))
            lignes.append((salon, b, mid, auteur, contenu, envoye,
                           modifie if modifie is not None else UNSET_VALUE,          # pas de NULL
                           json.dumps(pj, ensure_ascii=False) if pj is not None else UNSET_VALUE))
        for ok, _res in execute_concurrent_with_args(cs, ins, lignes, concurrency=64, raise_on_first_error=False):
            if ok:
                nb_ok += 1
            else:
                nb_echec += 1
    execute_concurrent_with_args(cs, ins_fenetre, sorted(fenetres), concurrency=64)
    print(f"cassandra : {nb_ok} écritures réussies, {nb_echec} en échec, {len(fenetres)} fenêtres (salon, bucket) non vides")

# ---------- Redis : présence (TTL 5 min) + ensembles de membres ----------
r = redis_conn()
r.flushdb()
rnd = random.Random(7)
users = [u for (u,) in pg.execute("SELECT id FROM app.utilisateur").fetchall()]
en_ligne = set(rnd.sample(users, int(len(users) * 0.3)))
pipe = r.pipeline()
for u in en_ligne:
    pipe.set(f"presence:{u}", "en_ligne", ex=300)
for serv, mem in pg.execute("SELECT serveur_id, array_agg(utilisateur_id) FROM app.membre GROUP BY 1").fetchall():
    pipe.sadd(f"membres:{serv}", *mem)
pipe.execute()
pg.execute("TRUNCATE app.presence")
with pg.cursor() as c:
    c.executemany("INSERT INTO app.presence VALUES (%s, now())", [(u,) for u in sorted(en_ligne)])
print(f"redis     : {r.dbsize()} clés ({len(en_ligne)} présences)")

# ---------- Neo4j : amitiés et appartenances ----------
drv = neo_driver()
with drv.session() as s:
    s.run("MATCH (n) DETACH DELETE n")
    s.run("CREATE CONSTRAINT utilisateur_id IF NOT EXISTS FOR (u:Utilisateur) REQUIRE u.id IS UNIQUE")
    s.run("CREATE CONSTRAINT serveur_id IF NOT EXISTS FOR (s:Serveur) REQUIRE s.id IS UNIQUE")
    s.run("UNWIND $ids AS i MERGE (:Utilisateur {id: i})", ids=users)
    s.run("UNWIND $ids AS i MERGE (:Serveur {id: i})", ids=[x for (x,) in pg.execute("SELECT id FROM app.serveur").fetchall()])
    am = pg.execute("SELECT utilisateur_a, utilisateur_b FROM app.amitie").fetchall()
    for i in range(0, len(am), 5000):
        s.run("UNWIND $rows AS r MATCH (a:Utilisateur {id: r[0]}), (b:Utilisateur {id: r[1]}) MERGE (a)-[:AMI_DE]->(b)", rows=[list(x) for x in am[i:i + 5000]])
    me = pg.execute("SELECT utilisateur_id, serveur_id FROM app.membre").fetchall()
    for i in range(0, len(me), 5000):
        s.run("UNWIND $rows AS r MATCH (u:Utilisateur {id: r[0]}), (v:Serveur {id: r[1]}) MERGE (u)-[:MEMBRE_DE]->(v)", rows=[list(x) for x in me[i:i + 5000]])
    for t in ("AMI_DE", "MEMBRE_DE"):
        print(f"neo4j     : {s.run(f'MATCH ()-[r:{t}]->() RETURN count(r) AS n').single()['n']} relations {t}")

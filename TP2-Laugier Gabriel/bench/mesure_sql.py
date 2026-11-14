"""Mesures 2 et 4 : durée des requêtes R1, R2, R3 (PostgreSQL), répétées pour avoir p50 / p95.

Un seul \\timing donne une seule valeur, qui dépend du hasard (cache, autre programme qui tourne...).
Ici chaque requête est lancée une fois « à froid » (durée notée), 20 fois pour chauffer le cache, puis N fois en chronométrant.

Usage : python mesure_sql.py [--n 200]
"""
import argparse
import statistics
import time

from common import pg_conn

SALONS = {"chaud": 775, "moyen": 424, "clairsemé": 342}
UTILISATEUR, SERVEUR = 7, 118

R1 = """SELECT m.id, u.pseudo, m.contenu, m.envoye_le
        FROM app.message m JOIN app.utilisateur u ON u.id = m.auteur_id
        WHERE m.salon_id = %s ORDER BY m.id DESC LIMIT 50"""
R2 = """SELECT EXISTS (SELECT 1 FROM app.salon s JOIN app.membre m ON m.serveur_id = s.serveur_id
                       WHERE s.id = %s AND s.type = 'texte' AND m.utilisateur_id = %s)"""
R3 = """SELECT (m.envoye_le AT TIME ZONE 'UTC')::date AS jour, count(*), count(DISTINCT m.auteur_id)
        FROM app.message m JOIN app.salon s ON s.id = m.salon_id
        WHERE s.serveur_id = %s AND m.envoye_le >= '2026-10-01 00:00:00+00' AND m.envoye_le < '2026-11-01 00:00:00+00'
        GROUP BY 1 ORDER BY 1"""
R3_SNOWFLAKE = """SELECT (m.envoye_le AT TIME ZONE 'UTC')::date AS jour, count(*), count(DISTINCT m.auteur_id)
        FROM app.message m JOIN app.salon s ON s.id = m.salon_id
        WHERE s.serveur_id = %s
          AND m.id >= ((extract(epoch FROM timestamptz '2026-10-01 00:00:00+00') * 1000)::bigint - 1420070400000) << 22
          AND m.id <  ((extract(epoch FROM timestamptz '2026-11-01 00:00:00+00') * 1000)::bigint - 1420070400000) << 22
        GROUP BY 1 ORDER BY 1"""
R3_VUE = """SELECT jour, nb_messages, nb_auteurs FROM app.activite_serveur_jour
            WHERE serveur_id = %s AND jour >= '2026-10-01' AND jour < '2026-11-01' ORDER BY jour"""


def chrono(pg, sql, params, n, echauffement=20):
    t0 = time.perf_counter()
    pg.execute(sql, params).fetchall()           # premier appel, « à froid »
    froid = (time.perf_counter() - t0) * 1000
    for _ in range(echauffement):
        pg.execute(sql, params).fetchall()
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        pg.execute(sql, params).fetchall()
        t.append((time.perf_counter() - t0) * 1000)
    t.sort()
    return statistics.median(t), t[int(len(t) * 0.95) - 1], froid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    n = ap.parse_args().n
    pg = pg_conn()
    lignes = []
    for nom, salon in SALONS.items():
        lignes.append((f"R1 50 derniers messages, salon {nom} ({salon})", *chrono(pg, R1, (salon,), n)))
    lignes.append(("R2 droit d'écrire", *chrono(pg, R2, (SALONS["chaud"], UTILISATEUR), n)))
    lignes.append(("R3 activité oct. 2026, calcul à la volée", *chrono(pg, R3, (SERVEUR,), n)))
    lignes.append(("R3 variante : filtre sur l'id Snowflake", *chrono(pg, R3_SNOWFLAKE, (SERVEUR,), n)))
    lignes.append(("R3 lue dans la vue matérialisée", *chrono(pg, R3_VUE, (SERVEUR,), n)))
    print(f"| Requête | p50 (ms) | p95 (ms) | 1er appel à froid (ms) |   (n = {n})\n|---|---:|---:|---:|")
    for nom, p50, p95, froid in lignes:
        print(f"| {nom} | {p50:.3f} | {p95:.3f} | {froid:.3f} |")


if __name__ == "__main__":
    main()

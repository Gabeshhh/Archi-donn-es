# Kit TP 2 — LAUGIER Gabriel

## Ce qu'il faut faire (une seule commande)

1. Copier **tout le contenu** de ce dossier dans `jour2/apprenant` (les fichiers `bench/bench.py` et `bench/load_nosql.py` fournis sont remplacés par les versions complétées ; `sql/00_staging.sql`, `bench/tombstones.py` et `data/` restent ceux du TP).
2. Lancer Docker Desktop (5 Go de mémoire).
3. Dans un terminal :
   ```bash
   cd jour2/apprenant
   source .venv/bin/activate
   bash tout_tester.sh
   ```

Durée : 10 à 15 minutes. À la fin :
- `resultats/` contient toutes les sorties brutes (une par étape) ;
- `tp-laugier/` est le dossier à rendre : `MESURES.md` et `DOSSIER_ARCHI.md` sont écrits avec les valeurs mesurées sur la machine, plus les SQL, CQL, scripts Python, `RUNBOOK_tombstones.md`, `mcd.md` et `architecture.md`.

Si une étape échoue, le script de rédaction s'arrête et dit laquelle ; la sortie complète est dans `resultats/`.

En fin de séance : `docker compose --profile colonne down -v`.

## Contenu

| Élément | Rôle |
|---|---|
| `sql/01` à `05` | schéma, chargement, requêtes, preuves des contraintes, coût du trigger |
| `cql/01_schema.cql` | tables Cassandra |
| `bench/` | `load_nosql.py`, `bench.py`, `verif_migration.py`, `mesure_sql.py`, `detail_b2b.py` (+ `common.py` fourni) |
| `tout_tester.sh` | lance tout et prépare `tp-laugier/` |
| `outils/` | modèles de `MESURES.md` / `DOSSIER_ARCHI.md` et `remplir_dossier.py` |
| `RUNBOOK_tombstones.md`, `mcd.md`, `architecture.md` | documents du livrable |

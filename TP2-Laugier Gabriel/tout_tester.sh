#!/usr/bin/env bash
# =====================================================================
# Lance TOUT le TP sur ma machine, range chaque sortie dans resultats/,
# puis écrit MESURES.md et DOSSIER_ARCHI.md avec les valeurs mesurées et
# prépare le dossier à rendre tp-laugier/.
# À lancer depuis jour2/apprenant, après y avoir copié le contenu du kit
# (sql/, cql/, bench/, outils/, ce script et les .md).
#
#   source .venv/bin/activate
#   bash tout_tester.sh
#
# Durée : 10 à 15 minutes (surtout Cassandra et tombstones.py).
# =====================================================================
set -u
cd "$(dirname "$0")"
R="$PWD/resultats"
mkdir -p "$R"
PSQL="docker compose exec -T postgres psql -U tp -d discord"

etape() { echo; echo "=== $* ==="; }

etape "Machine"
{
  echo "Processeur : $(sysctl -n machdep.cpu.brand_string 2>/dev/null || lscpu | grep 'Model name')"
  echo "Mémoire    : $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 )) Go"
  echo "Système    : $(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null) ($(uname -m))"
  echo "Docker     : $(docker --version)"
  echo "Mémoire Docker : $(docker info --format '{{.MemTotal}}' 2>/dev/null | awk '{printf "%.1f Go", $1/1073741824}')"
  echo "Python     : $(python --version 2>&1)"
} | tee "$R/00_machine.txt"

etape "Démarrage des 5 moteurs"
docker compose up -d
docker compose --profile colonne up -d
echo "Attente de Cassandra (1 à 2 min)..."
until docker compose --profile colonne exec -T cassandra cqlsh -e "DESCRIBE KEYSPACES" >/dev/null 2>&1; do sleep 5; done
echo "Attente de Neo4j..."
until docker compose exec -T neo4j cypher-shell -u neo4j -p tpynov2026 "RETURN 1" >/dev/null 2>&1; do sleep 3; done
docker compose --profile colonne ps | tee "$R/01_services.txt"

etape "Données (Mission 0)"
( cd data && [ -f messages.csv ] || python generate_data.py )
for f in data/*.csv; do echo "$f $(wc -l < "$f")"; done | tee "$R/02_wc.txt"

etape "Mission 1 et 2 : SQL"
$PSQL < sql/00_staging.sql            > "$R/10_staging.txt" 2>&1
$PSQL < sql/01_schema.sql             > "$R/11_schema.txt" 2>&1
$PSQL < sql/02_chargement.sql         > "$R/12_chargement.txt" 2>&1
$PSQL < sql/03_requetes.sql           > "$R/13_requetes.txt" 2>&1
$PSQL < sql/04_preuves_contraintes.sql > "$R/14_preuves.txt" 2>&1
$PSQL < sql/05_cout_trigger.sql       > "$R/15_cout_trigger.txt" 2>&1
grep -c ERROR "$R/14_preuves.txt" | sed 's/^/Refus obtenus (18 attendus) : /'
cat "$R/12_chargement.txt" | tail -40

cd bench

etape "Mesures 2 et 4"
python mesure_sql.py --n 300 2>&1 | tee "$R/20_mesure_sql.txt"

etape "Chargement MongoDB, Cassandra, Redis, Neo4j"
python load_nosql.py 2>&1 | tee "$R/30_load_nosql.txt"

etape "Mesures 5a et 5b"
python verif_migration.py 2>&1 | tee "$R/31_verif_migration.txt"

etape "Mesures 6, 7, 8 (bench.py --n 300)"
python bench.py --n 300 2>&1 | tee "$R/40_bench.txt"

etape "Détail de B2b côté Redis"
python detail_b2b.py --n 300 2>&1 | tee "$R/42_detail_b2b.txt"

etape "Expiration native Redis"
{
  docker compose exec -T redis redis-cli SET presence:test en_ligne EX 3
  docker compose exec -T redis redis-cli TTL presence:test
  sleep 4
  echo "4 secondes plus tard :"
  docker compose exec -T redis redis-cli TTL presence:test
  docker compose exec -T redis redis-cli EXISTS presence:test
} 2>&1 | tee "$R/41_expiration.txt"

etape "Mission 4 : l'incident"
python tombstones.py 2>&1 | tee "$R/50_tombstones.txt"
docker compose --profile colonne logs cassandra 2>&1 | grep -i tombstone | tail -5 | tee "$R/51_logs_tombstones.txt"

etape "Mission 4 : correctif 2 (purge des marqueurs existants)"
{
  CQL="docker compose --profile colonne exec -T cassandra cqlsh -e"
  $CQL "ALTER TABLE discord.messages_par_salon WITH gc_grace_seconds = 0;"
  docker compose --profile colonne exec -T cassandra nodetool flush discord messages_par_salon
  docker compose --profile colonne exec -T cassandra nodetool compact discord messages_par_salon
  python - <<'EOF'
import time
from common import cassandra_session
cs = cassandra_session("discord")
t0 = time.perf_counter()
rows = list(cs.execute("SELECT message_id FROM messages_par_salon WHERE salon_id = -1 AND bucket = 0 LIMIT 50"))
print(f"après purge : {len(rows)} message(s) lu(s) en {(time.perf_counter() - t0) * 1000:.0f} ms, ids = {[r.message_id for r in rows]}")
EOF
  $CQL "ALTER TABLE discord.messages_par_salon WITH gc_grace_seconds = 864000;"
  $CQL "SELECT gc_grace_seconds FROM system_schema.tables WHERE keyspace_name = 'discord' AND table_name = 'messages_par_salon';"
} 2>&1 | tee "$R/52_purge.txt"

etape "Mission 4 : correctif 1 (suppression de la partition entière)"
python tombstones.py --reparer 2>&1 | tee "$R/53_reparer.txt"

cd ..

etape "Rédaction de MESURES.md et DOSSIER_ARCHI.md, dossier à rendre"
rm -rf tp-laugier
mkdir -p tp-laugier/sql tp-laugier/cql tp-laugier/bench
cp RUNBOOK_tombstones.md mcd.md architecture.md tp-laugier/
cp sql/01_schema.sql sql/02_chargement.sql sql/03_requetes.sql sql/04_preuves_contraintes.sql sql/05_cout_trigger.sql tp-laugier/sql/
cp cql/01_schema.cql tp-laugier/cql/
cp bench/common.py bench/load_nosql.py bench/bench.py bench/verif_migration.py bench/mesure_sql.py bench/detail_b2b.py tp-laugier/bench/
python outils/remplir_dossier.py "$R" tp-laugier

echo
echo "Terminé. Résultats bruts : $R"
echo "Dossier à rendre        : $PWD/tp-laugier"
echo "Pour libérer la mémoire : docker compose --profile colonne down -v"

-- =====================================================================
-- TP 2 - Mission 3.2 : schéma Cassandra (l'état de 2017)
-- LAUGIER Gabriel - B3 Dev
-- Lancer avec :
--   docker compose --profile colonne exec -T cassandra cqlsh < cql/01_schema.cql
-- (load_nosql.py exécute aussi ces ordres, on peut donc s'en passer)
-- =====================================================================

-- Un seul nœud dans le TP : facteur de réplication 1.
-- (Discord : facteur 3, quorum. Ici on mesure le modèle de données, pas la tolérance aux pannes.)
CREATE KEYSPACE IF NOT EXISTS discord
  WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

-- Une table = une requête : « les 50 derniers messages d'un salon ».
-- Clé de partition (salon_id, bucket) : bucket = fenêtre de 10 jours calculée depuis l'id
--   (bucket_de() dans common.py). Une partition = un salon pendant 10 jours, ce qui la garde
--   sous les 100 Mo même pour un très gros salon (calcul 3.c du jour 1 : ≈ 82 Mo pour
--   10 millions de messages par an).
-- Clé de tri message_id (Snowflake, unique et trié dans le temps), du plus récent au plus ancien :
--   « LIMIT 50 » lit directement le début de la partition.
--   On ne trie PAS par horodatage : deux messages à la même milliseconde auraient la même clé
--   et le second écraserait le premier sans erreur.
-- Colonnes facultatives (modifie_le, pieces_jointes) : jamais écrites à NULL, on les omet
--   (UNSET_VALUE dans le pilote). Un NULL écrit = une tombstone.
CREATE TABLE IF NOT EXISTS discord.messages_par_salon (
    salon_id        bigint,
    bucket          int,
    message_id      bigint,
    auteur_id       bigint,
    contenu         text,
    envoye_le       timestamp,
    modifie_le      timestamp,
    pieces_jointes  text,          -- le JSON tel quel : sa forme varie selon le type
    PRIMARY KEY ((salon_id, bucket), message_id)
) WITH CLUSTERING ORDER BY (message_id DESC);

-- Correctif optionnel (mission 3.3) : la liste des fenêtres NON VIDES de chaque salon.
-- Une seule partition par salon, très petite (une ligne par fenêtre de 10 jours :
-- 36 lignes par an au plus). Écrite en même temps que le message (écriture idempotente :
-- réécrire la même ligne ne coûte rien de plus qu'une écriture normale).
-- La lecture B1 lit d'abord cette liste, puis seulement les fenêtres qui existent.
CREATE TABLE IF NOT EXISTS discord.fenetres_par_salon (
    salon_id  bigint,
    bucket    int,
    PRIMARY KEY (salon_id, bucket)
) WITH CLUSTERING ORDER BY (bucket DESC);

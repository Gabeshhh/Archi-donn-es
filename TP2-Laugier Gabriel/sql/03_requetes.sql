-- =====================================================================
-- TP 2 - Mission 2 : requêtes critiques et dénormalisations
-- LAUGIER Gabriel - B3 Dev
-- Lancer avec : psql ... -f sql/03_requetes.sql
-- Salons de test (mission 0) : 775 (chaud), 424 (moyen), 342 (clairsemé)
-- Serveur de test : 118 (le plus gros, 478 membres, contient le salon 775)
-- =====================================================================
\set salon 775
\set utilisateur 7
\set serveur 118
\timing on

-- ---------------------------------------------------------------------
-- R1 - Les 50 derniers messages d'un salon, avec le pseudo de l'auteur.
-- Pagination par clé (« keyset ») : on repart du plus petit id déjà affiché,
-- au lieu d'un OFFSET qui obligerait à relire et jeter toutes les lignes sautées.
-- Index utilisé : message_salon_id_idx (salon_id, id), lu à l'envers.
-- ---------------------------------------------------------------------
-- Première page
SELECT m.id, u.pseudo, m.contenu, m.envoye_le
FROM app.message m
JOIN app.utilisateur u ON u.id = m.auteur_id
WHERE m.salon_id = :salon
ORDER BY m.id DESC
LIMIT 50;

-- Page suivante : :dernier_id = le plus petit id de la page précédente
SELECT min(id) AS dernier_id FROM (
    SELECT id FROM app.message WHERE salon_id = :salon ORDER BY id DESC LIMIT 50
) p \gset
SELECT m.id, u.pseudo, m.contenu, m.envoye_le
FROM app.message m
JOIN app.utilisateur u ON u.id = m.auteur_id
WHERE m.salon_id = :salon
  AND m.id < :dernier_id
ORDER BY m.id DESC
LIMIT 50;

-- ---------------------------------------------------------------------
-- R2 - « L'utilisateur U peut-il écrire dans le salon S ? »
-- (salon textuel + U membre du serveur du salon). Exécutée à chaque message.
-- Deux lectures par clé primaire : salon (PK id) puis membre (PK utilisateur_id, serveur_id).
-- C'est la même vérification que le trigger trg_message_droit.
-- ---------------------------------------------------------------------
SELECT EXISTS (
    SELECT 1
    FROM app.salon s
    JOIN app.membre m ON m.serveur_id = s.serveur_id
    WHERE s.id = :salon
      AND s.type = 'texte'
      AND m.utilisateur_id = :utilisateur
) AS peut_ecrire;

-- ---------------------------------------------------------------------
-- R3 - Messages et auteurs distincts par jour, sur un serveur, pour octobre 2026.
-- Calcul à la volée (version normalisée).
-- ---------------------------------------------------------------------
SELECT (m.envoye_le AT TIME ZONE 'UTC')::date AS jour,
       count(*)                     AS nb_messages,
       count(DISTINCT m.auteur_id)  AS nb_auteurs
FROM app.message m
JOIN app.salon s ON s.id = m.salon_id
WHERE s.serveur_id = :serveur
  AND m.envoye_le >= '2026-10-01 00:00:00+00'
  AND m.envoye_le <  '2026-11-01 00:00:00+00'
GROUP BY 1
ORDER BY 1;

-- Mesure 3 : plan d'exécution de R3
EXPLAIN (ANALYZE, BUFFERS)
SELECT (m.envoye_le AT TIME ZONE 'UTC')::date AS jour,
       count(*), count(DISTINCT m.auteur_id)
FROM app.message m
JOIN app.salon s ON s.id = m.salon_id
WHERE s.serveur_id = :serveur
  AND m.envoye_le >= '2026-10-01 00:00:00+00'
  AND m.envoye_le <  '2026-11-01 00:00:00+00'
GROUP BY 1 ORDER BY 1;

-- Variante (sans dénormaliser) : l'id Snowflake contient la date.
-- On remplace le filtre sur envoye_le par un intervalle d'id, que l'index
-- (salon_id, id) sait lire directement. Même résultat, moins de lignes lues.
EXPLAIN (ANALYZE, BUFFERS)
SELECT (m.envoye_le AT TIME ZONE 'UTC')::date AS jour,
       count(*), count(DISTINCT m.auteur_id)
FROM app.message m
JOIN app.salon s ON s.id = m.salon_id
WHERE s.serveur_id = :serveur
  AND m.id >= ((extract(epoch FROM timestamptz '2026-10-01 00:00:00+00') * 1000)::bigint - 1420070400000) << 22
  AND m.id <  ((extract(epoch FROM timestamptz '2026-11-01 00:00:00+00') * 1000)::bigint - 1420070400000) << 22
GROUP BY 1 ORDER BY 1;

-- =====================================================================
-- DÉNORMALISATION 1 : vue matérialisée pour le tableau de bord (RG14)
-- Contrainte du dossier (2.c) : « reporting », agrégat sur beaucoup de messages,
-- toléré avec 24 h de retard.
-- =====================================================================
DROP MATERIALIZED VIEW IF EXISTS app.activite_serveur_jour;
CREATE MATERIALIZED VIEW app.activite_serveur_jour AS
SELECT s.serveur_id,
       (m.envoye_le AT TIME ZONE 'UTC')::date AS jour,
       count(*)::int                    AS nb_messages,
       count(DISTINCT m.auteur_id)::int AS nb_auteurs
FROM app.message m
JOIN app.salon s ON s.id = m.salon_id
GROUP BY 1, 2;

-- Index UNIQUE : sert la lecture (serveur + intervalle de jours)
-- ET est obligatoire pour REFRESH MATERIALIZED VIEW CONCURRENTLY.
CREATE UNIQUE INDEX activite_serveur_jour_uidx ON app.activite_serveur_jour (serveur_id, jour);
ANALYZE app.activite_serveur_jour;

-- R3 lue dans la vue (Mesure 4 : comparer avec R3 ci-dessus)
SELECT jour, nb_messages, nb_auteurs
FROM app.activite_serveur_jour
WHERE serveur_id = :serveur
  AND jour >= '2026-10-01' AND jour < '2026-11-01'
ORDER BY jour;

-- Rafraîchissement : une fois par nuit (tâche planifiée, cron ou pg_cron), par exemple à 3 h :
--   SELECT cron.schedule('0 3 * * *', 'REFRESH MATERIALIZED VIEW CONCURRENTLY app.activite_serveur_jour');
-- CONCURRENTLY : la vue reste lisible pendant le calcul (les lecteurs voient l'ancienne version),
-- au prix d'un calcul plus long (comparaison ancienne / nouvelle version).
-- Sans CONCURRENTLY, la vue est verrouillée : les lectures attendent la fin du calcul.
REFRESH MATERIALIZED VIEW CONCURRENTLY app.activite_serveur_jour;

-- Contrôle : la vue et le calcul à la volée donnent la même chose (doit renvoyer 0 ligne)
(SELECT jour, nb_messages, nb_auteurs FROM app.activite_serveur_jour
 WHERE serveur_id = :serveur AND jour >= '2026-10-01' AND jour < '2026-11-01')
EXCEPT
(SELECT (m.envoye_le AT TIME ZONE 'UTC')::date, count(*)::int, count(DISTINCT m.auteur_id)::int
 FROM app.message m JOIN app.salon s ON s.id = m.salon_id
 WHERE s.serveur_id = :serveur
   AND m.envoye_le >= '2026-10-01 00:00:00+00' AND m.envoye_le < '2026-11-01 00:00:00+00'
 GROUP BY 1);

-- =====================================================================
-- DÉNORMALISATION 2 : serveur.nb_membres (trigger dans 01_schema.sql)
-- Preuve : insertion, relecture, suppression, relecture -> retour à la valeur initiale.
-- =====================================================================
-- un utilisateur qui n'est pas encore membre du serveur
SELECT min(u.id) AS nouveau FROM app.utilisateur u
WHERE NOT EXISTS (SELECT 1 FROM app.membre m WHERE m.utilisateur_id = u.id AND m.serveur_id = :serveur) \gset

SELECT nb_membres AS avant FROM app.serveur WHERE id = :serveur;
INSERT INTO app.membre (utilisateur_id, serveur_id, role) VALUES (:nouveau, :serveur, 'membre');
SELECT nb_membres AS apres_insertion FROM app.serveur WHERE id = :serveur;
UPDATE app.membre SET role = 'moderateur' WHERE utilisateur_id = :nouveau AND serveur_id = :serveur;
SELECT nb_membres AS apres_changement_de_role FROM app.serveur WHERE id = :serveur;
DELETE FROM app.membre WHERE utilisateur_id = :nouveau AND serveur_id = :serveur;
SELECT nb_membres AS apres_suppression FROM app.serveur WHERE id = :serveur;

-- Cas UPDATE qui déplace un membre d'un serveur à un autre (-1 sur l'ancien, +1 sur le nouveau)
BEGIN;
INSERT INTO app.membre (utilisateur_id, serveur_id, role) VALUES (:nouveau, :serveur, 'membre');
SELECT id, nb_membres FROM app.serveur WHERE id IN (:serveur, 119) ORDER BY id;
UPDATE app.membre SET serveur_id = 119
WHERE utilisateur_id = :nouveau AND serveur_id = :serveur
  AND NOT EXISTS (SELECT 1 FROM app.membre WHERE utilisateur_id = :nouveau AND serveur_id = 119);
SELECT id, nb_membres FROM app.serveur WHERE id IN (:serveur, 119) ORDER BY id;
ROLLBACK;

-- Garde-fou (à lancer une fois par semaine) : trouver ET corriger les écarts
-- entre nb_membres et le vrai nombre. Renvoie les serveurs corrigés (0 ligne attendue).
UPDATE app.serveur s
SET nb_membres = c.vrai
FROM (SELECT serveur_id, count(*)::int AS vrai FROM app.membre GROUP BY serveur_id) c
WHERE c.serveur_id = s.id AND s.nb_membres <> c.vrai
RETURNING s.id, s.nb_membres;

\timing off

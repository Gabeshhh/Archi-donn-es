-- =====================================================================
-- TP 2 - Mission 1 : chargement staging.* -> app.*
-- LAUGIER Gabriel - B3 Dev
--
-- Une seule transaction : si une ligne est refusée, rien n'est chargé
-- (pas de base à moitié remplie).
-- Ordre imposé par les clés étrangères :
--   utilisateur -> serveur -> membre -> salon -> message -> amitie
-- La FK serveur -> membre (propriétaire membre) et le trigger RG3 sont différés :
-- ils sont vérifiés au COMMIT, une fois les membres chargés.
-- Les triggers restent actifs pendant le chargement : chaque message passe
-- le contrôle RG7/RG10, et nb_membres est calculé par le trigger lui-même.
-- =====================================================================
\timing on
BEGIN;

INSERT INTO app.utilisateur (id, pseudo, email, cree_le)
SELECT id::bigint, pseudo, email, cree_le::timestamptz
FROM staging.utilisateurs;

INSERT INTO app.serveur (id, nom, proprietaire_id, cree_le)
SELECT id::bigint, nom, proprietaire_id::bigint, cree_le::timestamptz
FROM staging.serveurs;

INSERT INTO app.membre (utilisateur_id, serveur_id, role, rejoint_le)
SELECT utilisateur_id::bigint, serveur_id::bigint, role, rejoint_le::timestamptz
FROM staging.membres;

INSERT INTO app.salon (id, serveur_id, nom, type, cree_le)
SELECT id::bigint, serveur_id::bigint, nom, type, cree_le::timestamptz
FROM staging.salons;

-- Dans le CSV, une valeur absente est une chaîne vide : on la transforme en NULL.
INSERT INTO app.message (id, salon_id, auteur_id, contenu, envoye_le, modifie_le, pieces_jointes)
SELECT id::bigint,
       salon_id::bigint,
       auteur_id::bigint,
       contenu,
       envoye_le::timestamptz,
       NULLIF(modifie_le, '')::timestamptz,
       NULLIF(pieces_jointes, '')::jsonb
FROM staging.messages;

INSERT INTO app.amitie (utilisateur_a, utilisateur_b, depuis)
SELECT utilisateur_a::bigint, utilisateur_b::bigint, depuis::timestamptz
FROM staging.amities;

COMMIT;
\timing off

ANALYZE app.utilisateur, app.serveur, app.membre, app.salon, app.message, app.amitie;

-- ---------------------------------------------------------------------
-- Mesure 1 : nombre de lignes app.* comparé à staging.* (l'écart doit être 0)
-- ---------------------------------------------------------------------
SELECT t.nom_table, t.staging, t.app, t.app - t.staging AS ecart
FROM (
              SELECT 'utilisateur' AS nom_table, (SELECT count(*) FROM staging.utilisateurs) AS staging, (SELECT count(*) FROM app.utilisateur) AS app
    UNION ALL SELECT 'serveur', (SELECT count(*) FROM staging.serveurs), (SELECT count(*) FROM app.serveur)
    UNION ALL SELECT 'membre',  (SELECT count(*) FROM staging.membres),  (SELECT count(*) FROM app.membre)
    UNION ALL SELECT 'salon',   (SELECT count(*) FROM staging.salons),   (SELECT count(*) FROM app.salon)
    UNION ALL SELECT 'message', (SELECT count(*) FROM staging.messages), (SELECT count(*) FROM app.message)
    UNION ALL SELECT 'amitie',  (SELECT count(*) FROM staging.amities),  (SELECT count(*) FROM app.amitie)
) t;

-- Contrôles en plus : rien n'a été perdu en route dans les colonnes facultatives
SELECT
    (SELECT count(*) FROM staging.messages WHERE pieces_jointes <> '') AS pj_staging,
    (SELECT count(*) FROM app.message WHERE pieces_jointes IS NOT NULL) AS pj_app,
    (SELECT count(*) FROM staging.messages WHERE modifie_le <> '')     AS modif_staging,
    (SELECT count(*) FROM app.message WHERE modifie_le IS NOT NULL)    AS modif_app;

-- ---------------------------------------------------------------------
-- Invariants (doivent tous renvoyer 0)
-- ---------------------------------------------------------------------
-- 1. Chaque propriétaire est membre de son serveur avec le rôle 'proprietaire'
SELECT count(*) AS serveurs_sans_proprietaire_membre
FROM app.serveur s
WHERE NOT EXISTS (SELECT 1 FROM app.membre m
                  WHERE m.serveur_id = s.id
                    AND m.utilisateur_id = s.proprietaire_id
                    AND m.role = 'proprietaire');

-- 2. Aucun message dans un salon vocal
SELECT count(*) AS messages_dans_salon_vocal
FROM app.message msg
JOIN app.salon sa ON sa.id = msg.salon_id
WHERE sa.type <> 'texte';

-- 3. Aucun message écrit par quelqu'un qui n'est pas membre du serveur du salon
SELECT count(*) AS messages_auteur_non_membre
FROM app.message msg
JOIN app.salon sa ON sa.id = msg.salon_id
WHERE NOT EXISTS (SELECT 1 FROM app.membre m
                  WHERE m.utilisateur_id = msg.auteur_id AND m.serveur_id = sa.serveur_id);

-- 4. nb_membres (tenu par trigger) = vrai nombre de membres
SELECT count(*) AS serveurs_nb_membres_faux
FROM app.serveur s
WHERE s.nb_membres <> (SELECT count(*) FROM app.membre m WHERE m.serveur_id = s.id);

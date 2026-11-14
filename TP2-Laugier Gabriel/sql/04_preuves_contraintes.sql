-- =====================================================================
-- TP 2 - Mission 1 : preuves des contraintes
-- Chaque tentative DOIT être refusée. Le message d'erreur obtenu est
-- recopié dans MESURES.md. Rien n'est modifié dans la base (ROLLBACK).
-- Lancer avec : psql ... -f sql/04_preuves_contraintes.sql
-- =====================================================================
\set ON_ERROR_STOP off
\echo '--- RG1 : e-mail déjà utilisé'
INSERT INTO app.utilisateur (id, pseudo, email) VALUES (99999, 'doublon', 'user1@exemple.fr');

\echo '--- RG3 : deux propriétaires dans le même serveur (serveur 118)'
UPDATE app.membre SET role = 'proprietaire'
WHERE serveur_id = 118 AND role = 'membre'
  AND utilisateur_id = (SELECT min(utilisateur_id) FROM app.membre WHERE serveur_id = 118 AND role = 'membre');

\echo '--- RG3 : supprimer la ligne membre du propriétaire'
DELETE FROM app.membre m USING app.serveur s
WHERE s.id = 118 AND m.serveur_id = s.id AND m.utilisateur_id = s.proprietaire_id;

\echo '--- RG3 : le propriétaire perd son rôle (vérifié au COMMIT)'
BEGIN;
UPDATE app.membre m SET role = 'moderateur' FROM app.serveur s
WHERE s.id = 118 AND m.serveur_id = s.id AND m.utilisateur_id = s.proprietaire_id;
COMMIT;

\echo '--- RG3 : créer un serveur dont le propriétaire n est pas membre'
BEGIN;
INSERT INTO app.serveur (id, nom, proprietaire_id) VALUES (9999, 'Serveur fantôme', 1);
COMMIT;

\echo '--- RG4 : rôle inconnu'
INSERT INTO app.membre (utilisateur_id, serveur_id, role) VALUES (1, 118, 'admin');

\echo '--- RG4 : deux fois membre du même serveur'
INSERT INTO app.membre (utilisateur_id, serveur_id, role)
SELECT utilisateur_id, serveur_id, 'membre' FROM app.membre WHERE serveur_id = 118 LIMIT 1;

\echo '--- RG6 : deux salons du même nom dans un serveur'
INSERT INTO app.salon (id, serveur_id, nom, type)
SELECT 99999, serveur_id, nom, 'texte' FROM app.salon WHERE id = 775;

\echo '--- RG6 : type de salon inconnu'
INSERT INTO app.salon (id, serveur_id, nom, type) VALUES (99999, 118, 'nouveau', 'video');

\echo '--- RG7 : message dans un salon vocal'
INSERT INTO app.message (id, salon_id, auteur_id, contenu, envoye_le)
SELECT 1, sa.id, s.proprietaire_id, 'test', now()
FROM app.salon sa JOIN app.serveur s ON s.id = sa.serveur_id
WHERE sa.type = 'vocal' ORDER BY sa.id LIMIT 1;

\echo '--- RG7 : un salon textuel avec des messages devient vocal'
UPDATE app.salon SET type = 'vocal' WHERE id = 775;

\echo '--- RG10 : auteur qui n est pas membre du serveur du salon'
INSERT INTO app.message (id, salon_id, auteur_id, contenu, envoye_le)
SELECT 1, 775, u.id, 'test', now()
FROM app.utilisateur u
WHERE NOT EXISTS (SELECT 1 FROM app.membre m WHERE m.utilisateur_id = u.id AND m.serveur_id = 118)
ORDER BY u.id LIMIT 1;

\echo '--- RG8 : date de modification avant la date d envoi'
UPDATE app.message SET modifie_le = envoye_le - interval '1 hour'
WHERE id = (SELECT min(id) FROM app.message);

\echo '--- RG8 : message sans contenu'
INSERT INTO app.message (id, salon_id, auteur_id, contenu, envoye_le)
SELECT 1, 775, s.proprietaire_id, NULL, now() FROM app.serveur s WHERE s.id = 118;

\echo '--- RG9 : pièce jointe d un type inconnu'
UPDATE app.message SET pieces_jointes = '{"type": "video", "nom": "film.mp4"}'
WHERE id = (SELECT min(id) FROM app.message);

\echo '--- RG11 : amitié avec soi-même'
INSERT INTO app.amitie (utilisateur_a, utilisateur_b) VALUES (42, 42);

\echo '--- RG11 : amitié stockée dans l autre sens (b, a)'
INSERT INTO app.amitie (utilisateur_a, utilisateur_b)
SELECT utilisateur_b, utilisateur_a FROM app.amitie ORDER BY 1, 2 LIMIT 1;

\echo '--- Clé étrangère : amitié avec un utilisateur qui n existe pas'
INSERT INTO app.amitie (utilisateur_a, utilisateur_b) VALUES (1, 999999);

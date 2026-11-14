-- =====================================================================
-- TP 2 - Mission 1 : schéma relationnel (PostgreSQL 16)
-- LAUGIER Gabriel - B3 Dev
--
-- Repris du MPD du jour 1, avec les noms de colonnes de l'annexe A.
-- Changement par rapport au jour 1 : la pièce jointe n'est plus une table
-- à part mais une colonne JSONB dans message (l'export fournit déjà un
-- objet JSON par message, et RG9 dit 0 ou 1 pièce jointe). C'était déjà
-- l'option retenue dans ma « version minimale » (4.e).
--
-- Règles de gestion (RG) du dossier et où elles sont garanties :
--   RG1  e-mail unique ............................ UNIQUE (email)
--   RG2  un serveur a au moins un membre .......... découle de RG3 (le propriétaire est membre)
--   RG3  un seul propriétaire, membre de son serveur
--        avec le rôle 'proprietaire' ............... proprietaire_id NOT NULL
--                                                    + FK (id, proprietaire_id) -> membre (différée)
--                                                    + index unique partiel (un seul rôle 'proprietaire')
--                                                    + trigger de contrainte différé (le rôle est le bon)
--   RG4  un seul rôle par membre, 3 valeurs ........ PK (utilisateur_id, serveur_id) + CHECK
--   RG5  un salon appartient à un serveur ......... FK NOT NULL
--   RG6  salon texte ou vocal, nom unique ......... CHECK + UNIQUE (serveur_id, nom)
--   RG7  message seulement dans un salon textuel .. trigger trg_message_droit (+ trigger sur salon)
--   RG8  auteur, contenu, date obligatoires ....... NOT NULL + CHECK (modifie_le >= envoye_le)
--   RG9  0 ou 1 pièce jointe, 3 types ............. colonne JSONB + CHECK sur le type
--   RG10 l'auteur est membre du serveur du salon .. trigger trg_message_droit
--   RG11 amitié une fois par paire, pas soi-même .. PK (a, b) + CHECK (a < b)
--   RG12 présence qui expire après 5 min .......... laissé au code : filtre vu_le > now() - 5 min
--                                                    (et Redis avec TTL, mission 5)
--   RG13 suggestions .............................. requêtes (mission 5), pas une contrainte
--   RG14 tableau de bord quotidien ................ vue matérialisée (03_requetes.sql)
-- =====================================================================

DROP SCHEMA IF EXISTS app CASCADE;
CREATE SCHEMA app;
SET search_path = app;

-- ---------------------------------------------------------------------
-- UTILISATEUR
-- ---------------------------------------------------------------------
CREATE TABLE utilisateur (
    id       BIGINT       PRIMARY KEY,                 -- BIGINT : Discord utilise des identifiants 64 bits
    pseudo   VARCHAR(32)  NOT NULL CHECK (char_length(pseudo) BETWEEN 2 AND 32),
    email    VARCHAR(254) NOT NULL UNIQUE               -- RG1. L'index créé sert aussi la connexion par e-mail
             CHECK (email LIKE '%_@_%'),
    cree_le  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- SERVEUR
-- nb_membres : dénormalisation (mission 2), tenue par trigger plus bas.
-- ---------------------------------------------------------------------
CREATE TABLE serveur (
    id               BIGINT       PRIMARY KEY,
    nom              VARCHAR(100) NOT NULL CHECK (char_length(nom) >= 1),
    proprietaire_id  BIGINT       NOT NULL REFERENCES utilisateur (id),   -- RG3 : exactement un propriétaire
    cree_le          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    nb_membres       INTEGER      NOT NULL DEFAULT 0 CHECK (nb_membres >= 0)
);
-- Pas d'index sur serveur(proprietaire_id) : 150 lignes, la table tient dans une page,
-- et la seule requête qui en aurait besoin (« les serveurs que je possède ») passe
-- aussi par membre(utilisateur_id, ...) avec role = 'proprietaire'.

-- ---------------------------------------------------------------------
-- MEMBRE (association EST MEMBRE)
-- ---------------------------------------------------------------------
CREATE TABLE membre (
    utilisateur_id  BIGINT      NOT NULL REFERENCES utilisateur (id) ON DELETE CASCADE,
    serveur_id      BIGINT      NOT NULL REFERENCES serveur (id)     ON DELETE CASCADE,
    role            VARCHAR(12) NOT NULL DEFAULT 'membre'
                    CHECK (role IN ('proprietaire', 'moderateur', 'membre')),     -- RG4
    rejoint_le      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (utilisateur_id, serveur_id)              -- RG4 : un seul rôle par (utilisateur, serveur)
);
-- La PK commence par utilisateur_id : elle sert R2 (« U est-il membre de S ? »)
-- et « les serveurs d'un utilisateur ». Elle ne sert pas « les membres d'un serveur » :
CREATE INDEX membre_serveur_idx ON membre (serveur_id);
-- -> liste des membres, B2b (en ligne dans un serveur), recalcul de nb_membres,
--    et index de la clé étrangère serveur_id (PostgreSQL ne l'indexe pas tout seul ;
--    sans lui, supprimer un serveur parcourt toute la table membre).

-- RG3 (partie 1) : au plus UN membre avec le rôle 'proprietaire' par serveur.
CREATE UNIQUE INDEX membre_un_proprietaire_uidx ON membre (serveur_id) WHERE role = 'proprietaire';

-- RG3 (partie 2) : le propriétaire est membre de son serveur.
-- Clé étrangère composée, vérifiée à la fin de la transaction (DEFERRABLE INITIALLY DEFERRED),
-- sinon on ne pourrait jamais créer un serveur (le membre ne peut pas exister avant le serveur).
-- Conséquence utile : on ne peut pas supprimer la ligne membre du propriétaire
-- (il doit d'abord transférer le serveur).
ALTER TABLE serveur
    ADD CONSTRAINT serveur_proprietaire_est_membre
    FOREIGN KEY (id, proprietaire_id) REFERENCES membre (serveur_id, utilisateur_id)
    DEFERRABLE INITIALLY DEFERRED;

-- ---------------------------------------------------------------------
-- SALON
-- ---------------------------------------------------------------------
CREATE TABLE salon (
    id          BIGINT       PRIMARY KEY,
    serveur_id  BIGINT       NOT NULL REFERENCES serveur (id) ON DELETE CASCADE,   -- RG5
    nom         VARCHAR(100) NOT NULL CHECK (char_length(nom) >= 1),
    type        VARCHAR(5)   NOT NULL CHECK (type IN ('texte', 'vocal')),           -- RG6
    cree_le     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (serveur_id, nom)                                                         -- RG6
);
-- Pas d'index en plus sur salon(serveur_id) : l'index de la contrainte UNIQUE (serveur_id, nom)
-- commence par serveur_id, il sert donc déjà la clé étrangère et « les salons d'un serveur » (R3).

-- ---------------------------------------------------------------------
-- MESSAGE
-- id = Snowflake : (ms depuis 2015-01-01) << 22 | compteur, donc unique ET trié dans le temps.
-- ---------------------------------------------------------------------
CREATE TABLE message (
    id              BIGINT      PRIMARY KEY,
    salon_id        BIGINT      NOT NULL REFERENCES salon (id) ON DELETE CASCADE,
    auteur_id       BIGINT      NOT NULL REFERENCES utilisateur (id),   -- pas de CASCADE : on ne veut pas
                                                                         -- effacer des milliers de messages
                                                                         -- d'un coup (question 4 au client)
    contenu         TEXT        NOT NULL CHECK (char_length(contenu) <= 2000),
    envoye_le       TIMESTAMPTZ NOT NULL,
    modifie_le      TIMESTAMPTZ,
    pieces_jointes  JSONB,
    CONSTRAINT message_modif_apres_envoi CHECK (modifie_le IS NULL OR modifie_le >= envoye_le),   -- RG8
    CONSTRAINT message_pas_vide          CHECK (contenu <> '' OR pieces_jointes IS NOT NULL),
    CONSTRAINT message_pj_valide CHECK (                                                           -- RG9
        pieces_jointes IS NULL
        OR (jsonb_typeof(pieces_jointes) = 'object'
            AND pieces_jointes ->> 'type' IN ('image', 'fichier', 'lien'))
    )
);
-- R1 / B1 (« les 50 derniers messages d'un salon ») : la requête la plus fréquente.
-- L'index donne les lignes d'un salon déjà triées par id ; PostgreSQL le lit à l'envers
-- (Index Scan Backward) et s'arrête à 50. Il sert aussi d'index pour la FK salon_id.
CREATE INDEX message_salon_id_idx ON message (salon_id, id);
-- Index de la FK auteur_id : retrouver les messages d'un utilisateur (suppression de compte,
-- modération). Sans lui, supprimer un utilisateur oblige à parcourir les 300 000 messages
-- pour vérifier la FK.
CREATE INDEX message_auteur_idx ON message (auteur_id);

-- ---------------------------------------------------------------------
-- AMITIE (association réflexive, stockée une seule fois par paire)
-- ---------------------------------------------------------------------
CREATE TABLE amitie (
    utilisateur_a  BIGINT      NOT NULL REFERENCES utilisateur (id) ON DELETE CASCADE,
    utilisateur_b  BIGINT      NOT NULL REFERENCES utilisateur (id) ON DELETE CASCADE,
    depuis         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (utilisateur_a, utilisateur_b),
    CONSTRAINT amitie_ordre CHECK (utilisateur_a < utilisateur_b)     -- RG11 : une fois par paire, pas soi-même
);
-- La PK sert « les amis de U » quand U est à gauche. Quand U est à droite :
CREATE INDEX amitie_b_idx ON amitie (utilisateur_b);   -- (+ index de la FK utilisateur_b)

-- ---------------------------------------------------------------------
-- PRESENCE (candidat « relationnel » du benchmark B2)
-- Pas d'expiration automatique en SQL : les lectures filtrent sur vu_le.
-- ---------------------------------------------------------------------
CREATE TABLE presence (
    utilisateur_id  BIGINT      PRIMARY KEY REFERENCES utilisateur (id) ON DELETE CASCADE,
    vu_le           TIMESTAMPTZ NOT NULL
);

-- =====================================================================
-- RÈGLES QUE LE MODÈLE NE PORTE PAS SEUL : triggers
-- =====================================================================

-- RG7 + RG10 : à chaque nouveau message (ou changement de salon / d'auteur),
-- le salon doit être textuel ET l'auteur membre du serveur du salon.
-- Choix : trigger plutôt que contrôle dans l'application, pour qu'un script d'import
-- ne puisse pas contourner la règle. Coût : 2 lectures par clé (salon par PK, membre par PK)
-- à chaque INSERT. Mesuré au chargement (voir MESURES.md, Mission 1).
CREATE FUNCTION verif_droit_message() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_type    text;
    v_serveur bigint;
BEGIN
    SELECT type, serveur_id INTO v_type, v_serveur FROM app.salon WHERE id = NEW.salon_id;
    IF v_type IS DISTINCT FROM 'texte' THEN
        RAISE EXCEPTION 'RG7 : le salon % n''est pas un salon textuel', NEW.salon_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.membre
                   WHERE utilisateur_id = NEW.auteur_id AND serveur_id = v_serveur) THEN
        RAISE EXCEPTION 'RG10 : l''utilisateur % n''est pas membre du serveur %', NEW.auteur_id, v_serveur
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_message_droit
    BEFORE INSERT OR UPDATE OF salon_id, auteur_id ON message
    FOR EACH ROW EXECUTE FUNCTION verif_droit_message();

-- RG7 (suite) : un salon qui contient des messages ne peut pas devenir vocal.
CREATE FUNCTION verif_type_salon() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.type = 'vocal' AND EXISTS (SELECT 1 FROM app.message WHERE salon_id = NEW.id) THEN
        RAISE EXCEPTION 'RG7 : le salon % contient des messages, il ne peut pas devenir vocal', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_salon_type
    BEFORE UPDATE OF type ON salon
    FOR EACH ROW WHEN (OLD.type IS DISTINCT FROM NEW.type)
    EXECUTE FUNCTION verif_type_salon();

-- RG3 (partie 3) : la ligne membre du propriétaire a bien le rôle 'proprietaire'.
-- Trigger de contrainte DIFFÉRÉ : vérifié au COMMIT, ce qui permet de créer un serveur
-- puis son membre propriétaire dans la même transaction, ou de transférer un serveur
-- (changer proprietaire_id + les deux rôles) sans état intermédiaire refusé.
CREATE FUNCTION verif_role_proprietaire() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_serveur bigint;
BEGIN
    IF TG_TABLE_NAME = 'serveur' THEN
        v_serveur := NEW.id;
    ELSIF TG_OP = 'DELETE' THEN
        v_serveur := OLD.serveur_id;
    ELSE
        v_serveur := NEW.serveur_id;
    END IF;
    IF EXISTS (SELECT 1 FROM app.serveur s
               WHERE s.id = v_serveur
                 AND NOT EXISTS (SELECT 1 FROM app.membre m
                                 WHERE m.serveur_id = s.id
                                   AND m.utilisateur_id = s.proprietaire_id
                                   AND m.role = 'proprietaire')) THEN
        RAISE EXCEPTION 'RG3 : le propriétaire du serveur % doit être membre avec le rôle proprietaire', v_serveur
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_serveur_role_proprietaire
    AFTER INSERT OR UPDATE OF proprietaire_id ON serveur
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION verif_role_proprietaire();

CREATE CONSTRAINT TRIGGER trg_membre_role_proprietaire
    AFTER UPDATE OF role ON membre
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION verif_role_proprietaire();
-- (La suppression de la ligne du propriétaire est déjà refusée par la FK composée.)

-- =====================================================================
-- DÉNORMALISATION 2 (mission 2) : serveur.nb_membres tenu par trigger.
-- Choix : INCRÉMENTER (+1 / -1) plutôt que recalculer un COUNT(*).
--   - INSERT : +1 sur le serveur ; DELETE : -1 ;
--   - UPDATE qui change serveur_id (rare) : -1 sur l'ancien, +1 sur le nouveau ;
--   - UPDATE du rôle seul : rien à faire (le trigger ne se déclenche pas).
-- Recalculer coûterait un COUNT(*) sur l'index membre_serveur_idx à chaque arrivée
-- (478 entrées pour le plus gros serveur ici, des centaines de milliers chez Discord).
-- Risque de l'incrément : si le trigger est désactivé un moment, l'écart reste pour
-- toujours -> garde-fou : requête de contrôle/correction dans 03_requetes.sql.
-- =====================================================================
CREATE FUNCTION maj_nb_membres() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        UPDATE app.serveur SET nb_membres = nb_membres + 1 WHERE id = NEW.serveur_id;
    END IF;
    IF TG_OP IN ('DELETE', 'UPDATE') THEN
        UPDATE app.serveur SET nb_membres = nb_membres - 1 WHERE id = OLD.serveur_id;
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER trg_membre_nb_membres_ins_del
    AFTER INSERT OR DELETE ON membre
    FOR EACH ROW EXECUTE FUNCTION maj_nb_membres();

CREATE TRIGGER trg_membre_nb_membres_upd
    AFTER UPDATE OF serveur_id ON membre
    FOR EACH ROW WHEN (OLD.serveur_id IS DISTINCT FROM NEW.serveur_id)
    EXECUTE FUNCTION maj_nb_membres();

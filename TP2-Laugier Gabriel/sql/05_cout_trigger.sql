-- =====================================================================
-- TP 2 - Mission 1 : coût du trigger RG7/RG10 sur l'écriture
-- Même insertion de 300 000 messages dans deux copies de la table
-- (mêmes index), avec et sans le trigger. Deux essais chacun.
-- Les copies sont supprimées à la fin.
-- =====================================================================
DROP TABLE IF EXISTS app.msg_sans_trigger, app.msg_avec_trigger;
CREATE TABLE app.msg_sans_trigger (LIKE app.message INCLUDING ALL);
CREATE TABLE app.msg_avec_trigger (LIKE app.message INCLUDING ALL);
CREATE TRIGGER trg_test BEFORE INSERT ON app.msg_avec_trigger
    FOR EACH ROW EXECUTE FUNCTION app.verif_droit_message();

\timing on
\echo 'essai 1 sans trigger'
INSERT INTO app.msg_sans_trigger SELECT * FROM app.message;
\echo 'essai 1 avec trigger'
INSERT INTO app.msg_avec_trigger SELECT * FROM app.message;
\timing off
TRUNCATE app.msg_sans_trigger, app.msg_avec_trigger;
\timing on
\echo 'essai 2 sans trigger'
INSERT INTO app.msg_sans_trigger SELECT * FROM app.message;
\echo 'essai 2 avec trigger'
INSERT INTO app.msg_avec_trigger SELECT * FROM app.message;
\timing off

DROP TABLE app.msg_sans_trigger, app.msg_avec_trigger;

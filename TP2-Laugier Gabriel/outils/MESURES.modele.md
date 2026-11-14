# MESURES — TP 2 : construire et mesurer le cœur de données

LAUGIER Gabriel — Bachelor 3 Dev — YNOV Montpellier — 2026-2027

Règle suivie partout : chaque phrase est soit un **fait** (mesure ou calcul que l'on peut refaire), soit une **hypothèse** écrite comme telle. Les durées dépendent de ma machine ; les dénombrements (nombres de lignes, de partitions, d'identifiants) n'en dépendent pas.

---

## Machine

| | |
|---|---|
| Processeur | «cpu» |
| Mémoire | «ram» |
| Système | «os» |
| Docker | «docker» (mémoire allouée : «docker_ram») |
| Moteurs | PostgreSQL 16, MongoDB 7, Redis 7, Neo4j 5, Cassandra 4.1 (images du `docker-compose.yml`) |
| Python | «python» |

Tous les moteurs tournent dans Docker sur la même machine que les scripts : un aller-retour client ↔ moteur ne traverse pas de vrai réseau.

---

## Données (Mission 0)

### Mesure 0 — nombre de lignes (`wc -l` moins l'en-tête)

| Fichier | `wc -l` | Lignes de données |
|---|---:|---:|
| utilisateurs.csv | 5 001 | 5 000 |
| serveurs.csv | 151 | 150 |
| salons.csv | 975 | 974 |
| membres.csv | 7 985 | 7 984 |
| messages.csv | 300 001 | 300 000 |
| amities.csv | 15 001 | 15 000 |

Commande :
```bash
for f in data/*.csv; do echo "---- $f ----"; wc -l < "$f"; done
```

### Répartition des messages par salon

Calcul en SQL sur `staging` :
```sql
WITH c AS (SELECT s.id, s.type, count(m.id) AS n
           FROM staging.salons s LEFT JOIN staging.messages m ON m.salon_id = s.id
           GROUP BY 1, 2)
SELECT min(n), percentile_disc(0.5) WITHIN GROUP (ORDER BY n) AS mediane, max(n),
       count(*) FILTER (WHERE n < 20) AS moins_de_20
FROM c;            -- ajouter WHERE type = 'texte' pour la 2e colonne du tableau
```

| Indicateur | Tous les salons (974) | Salons textuels seulement (747) |
|---|---:|---:|
| Minimum | 0 | 1 |
| Médiane | 41 | 97 |
| Maximum | 12 952 (salon 775) | 12 952 |
| Messages des 10 salons les plus actifs | 68 101 | 68 101 |
| Part du top 10 | 22,70 % | 22,70 % |
| Salons de moins de 20 messages | 375 (38,5 %) | 148 (19,8 %) |

**Correction de ma première analyse.** Mon premier calcul comptait les 227 salons **vocaux**, qui n'ont jamais de message (règle RG7). Ils tirent la médiane vers le bas (41 au lieu de 97) et gonflent le nombre de salons de moins de 20 messages (375 au lieu de 148). Pour décrire la charge des messages, la bonne population est celle des 747 salons textuels. La conclusion ne change pas : la répartition est très inégale. Les 10 salons les plus actifs (1,3 % des salons textuels) portent 22,7 % des messages.

Top 10 :

| Salon | Nom | Messages |
|---:|---|---:|
| 775 | hors-sujet | 12 952 |
| 779 | partage | 12 815 |
| 902 | general | 6 831 |
| 776 | off-topic | 6 484 |
| 780 | ressources | 6 466 |
| 120 | idees | 4 962 |
| 511 | projets | 4 836 |
| 781 | aide | 4 333 |
| 807 | hors-sujet | 4 260 |
| 24 | projets | 4 162 |

Période couverte : du 01/01/2026 07:37:13 au 31/10/2026 23:58:21, soit 303 jours (environ 10 mois).

### Trois salons de test

| Profil (dossier documentaire) | Salon | Serveur | Membres du serveur | Messages | Pourquoi ce choix |
|---|---:|---:|---:|---:|---|
| **Chaud** — gros serveur public | 775 `hors-sujet` | 118 | 478 (le plus gros) | 12 952 | Le salon le plus actif du plus gros serveur : la dernière heure est toujours lue, toujours en cache. |
| **Moyen** — petit serveur privé | 424 `projets` | 65 | 19 | 90 | Proche de la médiane des salons textuels (97). Un petit groupe d'amis qui écrit de temps en temps. |
| **Clairsemé** — serveur surtout vocal | 342 `annonces` | 52 | 4 | 1 | Le cas « un ou deux messages tous les quelques jours » : pour remplir une page, il faut remonter jusqu'à la création du salon. |

J'avais d'abord pris le salon 226 (41 messages) comme « moyen » parce que 41 était ma médiane. Avec la médiane corrigée (97), le salon 424 (90 messages) est plus représentatif.

### À décider

- **Les trois profils sont-ils là ?** Oui pour la **forme** : un serveur de 478 membres avec un salon à 12 952 messages, des petits serveurs privés autour de 100 messages par salon, et des serveurs presque silencieux (salon 342 : un seul message en 10 mois).
- **Ce qui manque : le volume et la durée.**
  - Volume : 300 000 messages, soit 46 Mo pour la table `message` avec ses index (fait : `pg_total_relation_size`). Le problème de 2015 apparaît quand les données ne tiennent plus en RAM (30 Go de données pour 16 Go de RAM, calcul 2.b du jour 1). Ici, tout tient en mémoire **sur tous les moteurs**.
  - Durée : 10 mois. La plus grosse partition Cassandra (salon 775, fenêtre 407) contient 2 673 messages (fait). On est très loin de la limite de 100 Mo par partition.
  - Il n'y a pas non plus de suppressions, pas d'écritures en même temps que les lectures, et un seul client.
- **Ce que je ne pourrai donc pas conclure :** que MongoDB « casse » (il ne peut pas casser si tout tient en RAM), qu'une partition devient trop grosse, ou comment les moteurs se comportent sous charge réelle (beaucoup d'utilisateurs en même temps, vrai réseau, plusieurs nœuds). Je pourrai par contre mesurer les **mécanismes** : nombre de partitions lues, nombre d'allers-retours, coût d'une écriture durable, effet d'un index.

---

## Mission 1 — Le socle relationnel

Fichiers : `sql/01_schema.sql`, `sql/02_chargement.sql`, `sql/04_preuves_contraintes.sql`, `sql/05_cout_trigger.sql`.

### Mesure 1 — lignes app.* contre staging.*

| Table | staging | app | Écart |
|---|---:|---:|---:|
«tableau_mesure1»

Contrôle en plus sur les colonnes facultatives : «pj_app» pièces jointes et «modif_app» dates de modification des deux côtés (aucune valeur perdue en route lors du passage de la chaîne vide à NULL).

Durée du chargement complet (une transaction, triggers actifs) : «t_load_total» s, dont «t_load_msg» s pour les 300 000 messages.

### Coût du trigger RG7/RG10 sur l'écriture

Même insertion de 300 000 messages dans deux copies de la table (mêmes index), avec et sans le trigger, deux essais (`sql/05_cout_trigger.sql`) :

| | Essai 1 | Essai 2 |
|---|---:|---:|
| Sans trigger | «trig_sans1» ms | «trig_sans2» ms |
| Avec trigger | «trig_avec1» ms | «trig_avec2» ms |

Coût du trigger ≈ («trig_avec2» − «trig_sans2») / 300 000 ≈ **«trig_us» µs par message**, soit environ ×«trig_x» sur un chargement en masse. Sur l'envoi d'un message à l'unité, c'est négligeable à côté d'un aller-retour (R2 seule prend «r2_p50» ms côté client, voir Mesure 2).

### Preuves des contraintes («nb_refus» tentatives, «nb_refus» refus)

Script : `sql/04_preuves_contraintes.sql`. Messages d'erreur recopiés tels quels :

| Règle | Tentative | Message d'erreur |
|---|---|---|
| RG1 | e-mail déjà pris | `duplicate key value violates unique constraint "utilisateur_email_key"` — `Key (email)=(user1@exemple.fr) already exists.` |
| RG3 | 2e propriétaire dans le serveur 118 | `duplicate key value violates unique constraint "membre_un_proprietaire_uidx"` — `Key (serveur_id)=(118) already exists.` |
| RG3 | supprimer la ligne membre du propriétaire | `update or delete on table "membre" violates foreign key constraint "serveur_proprietaire_est_membre" on table "serveur"` — `Key (serveur_id, utilisateur_id)=(118, 7) is still referenced from table "serveur".` |
| RG3 | le propriétaire passe modérateur (refusé au COMMIT) | `RG3 : le propriétaire du serveur 118 doit être membre avec le rôle proprietaire` |
| RG3 | serveur dont le propriétaire n'est pas membre | `insert or update on table "serveur" violates foreign key constraint "serveur_proprietaire_est_membre"` — `Key (id, proprietaire_id)=(9999, 1) is not present in table "membre".` |
| RG4 | rôle `admin` | `new row for relation "membre" violates check constraint "membre_role_check"` |
| RG4 | deux fois membre du même serveur | `duplicate key value violates unique constraint "membre_pkey"` — `Key (utilisateur_id, serveur_id)=(7, 118) already exists.` |
| RG6 | deux salons `hors-sujet` dans le serveur 118 | `duplicate key value violates unique constraint "salon_serveur_id_nom_key"` |
| RG6 | type `video` | `new row for relation "salon" violates check constraint "salon_type_check"` |
| RG7 | message dans un salon vocal | `RG7 : le salon 9 n'est pas un salon textuel` |
| RG7 | le salon 775 (avec messages) devient vocal | `RG7 : le salon 775 contient des messages, il ne peut pas devenir vocal` |
| RG10 | auteur non membre du serveur | `RG10 : l'utilisateur 1 n'est pas membre du serveur 118` |
| RG8 | modifié avant d'être envoyé | `new row for relation "message" violates check constraint "message_modif_apres_envoi"` |
| RG8 | contenu NULL | `null value in column "contenu" of relation "message" violates not-null constraint` |
| RG9 | pièce jointe de type `video` | `new row for relation "message" violates check constraint "message_pj_valide"` |
| RG11 | ami avec soi-même | `new row for relation "amitie" violates check constraint "amitie_ordre"` |
| RG11 | paire stockée dans l'autre sens (2, 1) | `new row for relation "amitie" violates check constraint "amitie_ordre"` |
| FK | ami avec l'utilisateur 999999 | `insert or update on table "amitie" violates foreign key constraint "amitie_utilisateur_b_fkey"` |

### Invariants (requêtes dans `02_chargement.sql`)

| Contrôle | Résultat |
|---|---:|
| Serveurs dont le propriétaire n'est pas membre avec le rôle « propriétaire » | «inv1» |
| Messages dans un salon vocal | «inv2» |
| Messages dont l'auteur n'est pas membre du serveur du salon | «inv3» |
| Serveurs où `nb_membres` ≠ vrai nombre de membres | «inv4» |

### À décider : comment garantir les règles que le modèle ne porte pas seul ?

| Règle | Choix | Pourquoi | Coût sur le chemin d'écriture |
|---|---|---|---|
| Propriétaire membre (RG3) | Contraintes déclaratives : FK composée différée `(id, proprietaire_id) → membre` + index unique partiel `WHERE role = 'proprietaire'`, et un trigger de contrainte différé pour le rôle | Le plus possible en déclaratif : c'est vérifié par le moteur, impossible à oublier. Le trigger ne sert qu'au rôle. | Seulement à la création d'un serveur ou au changement de rôle : écritures rares. |
| Message seulement dans un salon textuel (RG7) + droit d'écrire (RG10) | Trigger `BEFORE INSERT` sur `message` | Un script d'import ou un deuxième service ne peut pas passer à côté. | 2 lectures par clé primaire par message : ≈ «trig_us» µs mesurées. C'est le chemin le plus fréquent, mais le coût est faible. |
| Contrôle dans l'application seul | Écarté pour RG7/RG10 | Plus rapide (pas de travail dans la base) mais chaque nouveau service doit le recoder ; un oubli = des messages interdits. | 0 dans la base, mais la même requête R2 côté application. |
| Requête de détection périodique | Gardée **en plus** (les invariants ci-dessus, chaque nuit) | Ne protège pas (elle constate après coup) mais détecte un trigger désactivé ou un import fait à côté. | Aucun sur l'écriture, un parcours de table la nuit. |

Remarque : si les messages partent un jour dans Cassandra (pas de trigger, pas de FK), RG7/RG10 devront passer dans l'application, avec R2 lue dans PostgreSQL avant chaque écriture.

---

## Mission 2 — Tenir les objectifs de latence

Fichier : `sql/03_requetes.sql`. Un seul `\timing` donne une seule valeur, qui dépend du hasard ; les durées ci-dessous viennent de `bench/mesure_sql.py` : un premier appel noté « à froid », 20 appels d'échauffement, puis 300 appels chronométrés.

### Mesure 2 — R1, R2, R3

| Requête | p50 (ms) | p95 (ms) | 1er appel (ms) |
|---|---:|---:|---:|
| R1 50 derniers messages + pseudo, salon chaud (775) | «r1c_p50» | «r1c_p95» | «r1c_froid» |
| R1 salon moyen (424) | «r1m_p50» | «r1m_p95» | «r1m_froid» |
| R1 salon clairsemé (342) | «r1s_p50» | «r1s_p95» | «r1s_froid» |
| R2 droit d'écrire | «r2_p50» | «r2_p95» | «r2_froid» |
| R3 activité d'octobre, serveur 118 (calcul à la volée) | «r3_p50» | «r3_p95» | «r3_froid» |

Objectifs du dossier : R1 p95 < 80 ms → «verdict_r1» ; R2 < 5 ms → «verdict_r2».

### Mesure 3 — EXPLAIN (ANALYZE, BUFFERS) de R3

Nœud le plus coûteux (recopié) :
```
«m3_bloc»
```

- **«m3_type»** «m3_type_explication»
- **Pourquoi l'index ne suffit pas** : il ne contient pas `envoye_le`. PostgreSQL doit donc aller lire **dans la table** chacun des messages des 4 salons textuels du serveur 118 («m3_lus» lignes au total), regarder la date, et jeter celles qui ne sont pas d'octobre («m3_jetes_pct» % des lignes lues). Il ne garde que 1 928 messages.
- **Piste sans dénormaliser** : l'id Snowflake contient la date. En filtrant sur un intervalle d'id au lieu de `envoye_le`, l'index lit directement les messages d'octobre : «m3sf_buf» pages au lieu de «m3_buf», «r3sf_p50» ms au lieu de «r3_p50» ms (p50).

### Deux dénormalisations

**(1) Vue matérialisée `app.activite_serveur_jour`** — contrainte du dossier (2.c) : reporting, agrégat sur beaucoup de messages, 24 h de retard acceptées.

### Mesure 4 — R3 avant / après vue matérialisée

| Version | p50 (ms) | p95 (ms) |
|---|---:|---:|
| Calcul à la volée | «r3_p50» | «r3_p95» |
| Calcul à la volée, filtre sur l'id Snowflake | «r3sf_p50» | «r3sf_p95» |
| Lecture dans la vue matérialisée | «r3v_p50» | «r3v_p95» |
| Pour comparaison : MongoDB `aggregate` (index `serveur_id, envoye_le`) | «b4_mongo_p50» | «b4_mongo_p95» |

Gain de la vue : ×«f_vue_p50» au p50, ×«f_vue_p95» au p95. Contrôle : la vue et le calcul à la volée donnent exactement les mêmes 31 lignes (requête `EXCEPT` : 0 ligne). «phrase_b4_mongo»

- **Fraîcheur** : la vue est à jour au moment du dernier `REFRESH`. Avec un rafraîchissement par nuit, elle a jusqu'à 24 h de retard (accepté par RG14).
- **Commande** : `REFRESH MATERIALIZED VIEW CONCURRENTLY app.activite_serveur_jour;` («t_refresh» ms mesurées). `CONCURRENTLY` exige l'index unique `(serveur_id, jour)`.
- **Qui la déclenche** : une tâche planifiée la nuit (cron ou `pg_cron`, par exemple à 3 h), pas l'application.
- **Pendant le rafraîchissement** : avec `CONCURRENTLY`, les lectures continuent sur l'ancienne version, puis voient la nouvelle d'un coup. Sans `CONCURRENTLY` («t_mv_create» ms pour le calcul initial), la vue est verrouillée : les lectures attendent la fin.

**(2) Colonne `serveur.nb_membres` tenue par trigger** — contrainte du dossier : lecture intensive (affichée sur chaque page du serveur).

Preuve (dans `03_requetes.sql`) :

| Étape | nb_membres du serveur 118 |
|---|---:|
| Avant | «nb_avant» |
| Après INSERT d'un membre | «nb_ins» |
| Après UPDATE du rôle (aucun changement attendu) | «nb_role» |
| Après DELETE de ce membre | **«nb_del»** (retour à la valeur initiale) |
| Cas « déplacement » : UPDATE serveur_id 118 → 119 | 118 : 479 → 478, 119 : 478 → 479 |

Requête garde-fou (recalcul et correction) : 0 serveur corrigé.

### À décider

**Incrémenter ou recalculer ?** J'incrémente (+1 / −1).

| | Incrément (choisi) | Recalcul `COUNT(*)` |
|---|---|---|
| INSERT | +1 : une écriture de ligne | compte tous les membres du serveur (478 ici, des centaines de milliers chez Discord) |
| DELETE | −1 | recompte |
| UPDATE du rôle | rien (trigger non déclenché) | rien si le trigger est limité à `serveur_id` |
| UPDATE qui change de serveur | −1 sur l'ancien, +1 sur le nouveau | recompte les deux |
| Si le trigger rate une fois (désactivé, import à côté) | l'écart reste pour toujours | se corrige tout seul au changement suivant |

D'où le garde-fou : recalcul hebdomadaire qui corrige les écarts. Autre risque de l'incrément : chaque arrivée verrouille la ligne du serveur ; si des milliers de personnes rejoignent en même temps un gros serveur, elles attendent les unes après les autres (pas mesurable ici : un seul client).

**Tableau des coûts**

| | Gain en lecture | Coût payé | Risque d'incohérence | Garde-fou |
|---|---|---|---|---|
| Vue matérialisée `activite_serveur_jour` | R3 : «r3_p50» → «r3v_p50» ms (×«f_vue_p50») | «t_refresh» ms de calcul par nuit, 28 957 lignes stockées, une tâche planifiée à surveiller | Jusqu'à 24 h de retard ; si le rafraîchissement échoue, les chiffres vieillissent sans bruit | Afficher « mis à jour le … » ; alerte si le dernier rafraîchissement a plus de 26 h ; contrôle `EXCEPT` sur un serveur tiré au hasard |
| Colonne `nb_membres` | un `COUNT(*)` sur 478 lignes d'index remplacé par la lecture d'une colonne | 1 UPDATE en plus à chaque arrivée/départ ; verrou sur la ligne du serveur | Écart permanent si une modification passe sans le trigger | `CHECK (nb_membres >= 0)` + recalcul/correction hebdomadaire + invariant n°4 chaque nuit |

**Avis honnête** : à ce volume, R3 à la volée («r3_p50» ms) est déjà largement sous l'objectif de « quelques secondes ». La vue n'est **pas encore justifiée par la latence** ; elle l'est par la croissance attendue (le calcul à la volée grandit avec le nombre de messages du serveur, la lecture de la vue non).

---

## Mission 3 — Les messages : rejouer 2015, puis 2017

Fichiers : `bench/load_nosql.py`, `cql/01_schema.cql`, `bench/verif_migration.py`, `bench/bench.py`.

Sortie du chargement :
```
«sortie_load»
```

### Choix MongoDB (2015)

- **Champs embarqués** : `_id` (= id Snowflake), `salon_id`, `serveur_id`, `auteur_id`, `contenu`, `envoye_le`, et seulement s'ils existent `modifie_le` et `pieces_jointes`. Pas de pseudo : il peut changer, il reste dans PostgreSQL.
- **Pièce jointe** : un **sous-document** dans le message. Elle n'existe jamais sans son message, elle est toujours lue avec lui, et sa forme change selon le type (image : largeur/hauteur ; fichier : octets ; lien : url, titre…). C'est le cas idéal d'un document. Une collection à part obligerait à une 2e lecture pour rien.
- **Index** :
  - `{salon_id: 1, _id: -1}` → B1 : égalité sur le salon puis tri par id décroissant ; les 50 premiers documents de l'index sont la réponse.
  - `{serveur_id: 1, envoye_le: 1}` → B4 : égalité sur le serveur puis intervalle de dates.
- Taille : «mongo_taille» (`collStats`). Plan de B1 (`explain()`, de la sortie vers la source) : `«mongo_plan»`. «phrase_mongo_plan»

### Choix Cassandra (2017)

`PRIMARY KEY ((salon_id, bucket), message_id)` avec `CLUSTERING ORDER BY (message_id DESC)`, comme décidé au jour 1 (phase 3.c). `bucket` = fenêtre de 10 jours calculée depuis l'id. Les colonnes absentes ne sont jamais écrites (`UNSET_VALUE`), donc pas de tombstone au chargement.

### Mesure 5a — même nombre de messages partout

Commande : `python verif_migration.py`. Pour Cassandra, je n'utilise pas `SELECT count(*)` sur toute la table : sans clé de partition, le coordinateur doit lire **toutes les partitions de tous les nœuds** ; sur une vraie table cela dépasse le délai d'attente et charge tout le cluster. Je fais à la place un `count(*)` **dans chaque partition** (salon, fenêtre), qui ne lit qu'elle, et je compare salon par salon avec PostgreSQL.

| Moteur | Messages | Salons dont le compte diffère de PostgreSQL |
|---|---:|---:|
| PostgreSQL | 300 000 | — |
| MongoDB | «m5a_mongo» | «m5a_mongo_diff» |
| Cassandra (somme de 20 532 `count(*)` par partition, 15 965 partitions non vides) | «m5a_cs» | «m5a_cs_diff» |

### Mesure 5b — dark launch : les 50 mêmes ids, dans le même ordre

| Salon | Messages lus | Premier id (le plus récent) | Dernier id | PG = Mongo | PG = Cassandra |
|---|---:|---|---|:-:|:-:|
| chaud (775) | 50 | 1566236554475901907 | 1565815739628425005 | «m5b_c_mongo» | «m5b_c_cs» |
| moyen (424) | 50 | 1565571041806552170 | 1505342927315012592 | «m5b_m_mongo» | «m5b_m_cs» |
| clairsemé (342) | 1 | 1476515388547186697 | 1476515388547186697 | «m5b_s_mongo» | «m5b_s_cs» |

Les mêmes vérifications sont faites en `assert` au début de `bench.py` (ainsi que pour le correctif) : si une liste diffère, le benchmark s'arrête avant de chronométrer.

### Combien de partitions Cassandra sont lues ? (fait, indépendant de la machine)

`b1_cassandra` interroge les fenêtres l'une après l'autre, de la plus récente à la plus ancienne, jusqu'à avoir 50 messages ou atteindre la fenêtre de création du salon. Nombre de fenêtres lues relevé par `bench.py` : «fen_c» (chaud), «fen_m» (moyen), «fen_s» (clairsemé).

| Salon | Messages | Fenêtres de sa vie (création → dernière) | Fenêtres non vides | Requêtes sans correctif | Requêtes avec correctif |
|---|---:|---:|---:|---:|---:|
| chaud (775) | 12 952 | 26 | 26 | **1** (79 messages dans la dernière fenêtre) | 1 + 1 (liste) |
| moyen (424) | 90 | 29 | 26 | **18** | 17 + 1 |
| clairsemé (342) | 1 | 26 | 1 | **26** | 1 + 1 |

### Mesure 6 — B1 : p50 / p95 par moteur et par salon (ms, n = 300)

| Salon | PostgreSQL | MongoDB | Cassandra | Cassandra + correctif |
|---|---:|---:|---:|---:|
| chaud | «b1_c_pg» | «b1_c_mongo» | «b1_c_cs» | «b1_c_csf» |
| moyen | «b1_m_pg» | «b1_m_mongo» | «b1_m_cs» | «b1_m_csf» |
| clairsemé | «b1_s_pg» | «b1_s_mongo» | «b1_s_cs» | «b1_s_csf» |

(PostgreSQL : `bench.py` ne lit que les ids, d'où des durées plus courtes que R1.)

«phrase_mongo_b1»

### Question : l'écart entre le salon chaud et le salon clairsemé est-il le même pour les trois moteurs ?

Rapport clairsemé / chaud au p50 : PostgreSQL ×«r_s_c_pg», MongoDB ×«r_s_c_mongo», **Cassandra ×«r_s_c_cs»**.

- **PostgreSQL et MongoDB** : un seul parcours d'index `(salon, id)` lu à l'envers, arrêté à 50. Le profil du salon ne rend pas la lecture plus lente ; le salon clairsemé n'a qu'une ligne à renvoyer.
- **Cassandra** : «phrase_cs_profil»
- **Le mécanisme** : le salon clairsemé demande **26 requêtes** au lieu d'**une** pour le salon chaud. Comme on ne sait pas d'avance où sont les messages, les requêtes sont faites l'une après l'autre : le temps total ≈ somme de 26 allers-retours. Le moteur et la machine étant les mêmes, l'écart ne vient que du nombre de partitions lues.
- **Lien avec le calcul 3.c du jour 1** : j'avais trouvé 2 partitions pour un salon de 1 000 messages par an, et ≈ 19 pour un salon de 100 messages par an. Le salon 342 est encore pire : avec un seul message, une page n'est **jamais** remplie, donc la lecture remonte **toujours** jusqu'à la création du salon. C'est le cas « serveur vocal » de l'article.

### Correctif (optionnel) — `b1_cassandra_fenetres`

Mécanisme : une petite table `fenetres_par_salon (salon_id, bucket)` qui liste les fenêtres non vides de chaque salon, triées de la plus récente à la plus ancienne. On la lit d'abord (1 requête, une seule petite partition), puis seulement les fenêtres qui existent.

- **Mesuré** : clairsemé «b1_s_cs_p50» → «b1_s_csf_p50» ms ; moyen «b1_m_cs_p50» → «b1_m_csf_p50» ms ; chaud «b1_c_cs_p50» → «b1_c_csf_p50» ms (p50).
- **Coût** : une écriture en plus à chaque message (une ligne `(salon, fenêtre)` réécrite à l'identique la plupart du temps ; l'application peut la sauter si elle sait que la fenêtre est déjà notée). Le coût est payé **à l'écriture**, par tous les messages, pour accélérer certaines lectures. Autre coût : si on supprime le dernier message d'une fenêtre, la ligne reste (lecture inutile) sauf si on la nettoie, ce qui crée… une tombstone.
- **Clairsemé** : 26 requêtes → 2. «phrase_correctif_s»
- **Moyen** : 18 → 18 (17 + 1). «phrase_correctif_m» Ses fenêtres sont presque toutes non vides (26 sur 29) : il écrit un peu chaque semaine. Le correctif ne supprime que les lectures de fenêtres vides ; ici le problème est qu'il faut **beaucoup de fenêtres pleines** pour réunir 50 messages. Pour lui, il faudrait des fenêtres plus larges (N plus grand pour les salons peu actifs).
- **Chaud** : 1 → 2 requêtes (une lecture de liste en plus). «phrase_correctif_c»

### À décider

- **MongoDB est-il défaillant ici ?** Non, et il ne peut pas l'être : «mongo_taille», tout tient en RAM. Il répond en «b1_c_mongo_p50» ms au p50 pour le salon chaud, très loin des 80 ms, et sa durée ne dépend pas du profil du salon.
- **Cassandra est-elle meilleure ?** «phrase_cs_meilleure» Son avantage (données rangées par salon sur le disque, ajout de nœuds) ne sert que quand les données dépassent la RAM.
- **Que faut-il pour que la réponse change ?** Reprise du calcul de sensibilité du jour 1 : il faut que les données dépassent la mémoire. Avec ≈ 83 octets par ligne mesurés dans PostgreSQL (`pg_column_size`, hors index) au lieu des 300 supposés, 16 Go de RAM correspondent à environ 190 millions de messages en table seule ; avec les index et le reste, plutôt **100 à 150 millions** (hypothèse). Soit environ 400 fois le volume du TP. Ça rejoint le seuil de l'article (≈ 100 millions de messages fin 2015).

---

## Mission 4 — L'incident : le salon aux millions de suppressions

Fichier : `RUNBOOK_tombstones.md`.

### Observation

```bash
cd bench && python tombstones.py
docker compose --profile colonne logs cassandra | grep -i tombstone
```

Sortie du script :
```
«tomb_sortie»
```

Ligne(s) du journal Cassandra :
```
«tomb_log»
```

### Le mécanisme en cinq lignes

1. Une suppression dans Cassandra n'efface rien tout de suite : elle écrit un **marqueur** (tombstone), et la lecture doit le lire pour savoir que la ligne est morte.
2. Les messages sont triés du plus récent au plus ancien ; le seul message restant (id 1) est **le plus ancien**, donc le **dernier** de la partition : pour le trouver, la lecture traverse les 119 999 marqueurs.
3. Les marqueurs sont gardés `gc_grace_seconds` (10 jours par défaut) : sinon, un nœud qui a raté la suppression pourrait « ressusciter » la donnée lors d'une réparation.
4. Tous ces marqueurs sont chargés en mémoire pendant la lecture : des millions de marqueurs = mémoire saturée et pauses du ramasse-miettes (les pauses de 10 s de l'article).
5. Au-delà de `tombstone_failure_threshold` (100 000 par défaut), Cassandra **refuse** la lecture : mieux vaut une erreur claire pour une requête qu'un nœud entier qui tombe pour tout le monde.

### Deux correctifs

**(1) `python tombstones.py --reparer`** — supprime la **partition entière** en une seule requête : **un** marqueur de partition au lieu de 119 999 marqueurs de ligne. La lecture voit tout de suite que la partition est vide.
```
«reparer_sortie»
```
Suppose qu'on veuille vraiment tout effacer dans cette fenêtre (purge d'un salon, d'une période). Risque : efface aussi les messages qu'on voulait garder (il faut les réécrire après) ; ne règle rien pour les marqueurs déjà présents.

**(2) Purge des marqueurs existants** — `gc_grace_seconds = 0`, `nodetool flush`, `nodetool compact`, puis remise à `864000`. La compaction réécrit les fichiers sans les marqueurs : la lecture ne les traverse plus.
```
«purge_sortie»
```
Suppose un seul nœud, ou tous les nœuds à jour (réparation faite juste avant). Risques : sur plusieurs nœuds, un nœud qui avait raté la suppression peut faire **revenir** les messages supprimés ; la compaction complète est lourde (disque, CPU) ; oublier de remettre `gc_grace_seconds` laisse le risque ouvert pour toujours.

### À décider

- **Ce qu'il fallait prévoir à la conception** (mon dossier, phase 3.d, avait déjà noté qu'« une suppression n'est pas gratuite ») : pour les suppressions en masse, **masquer** au lieu de supprimer (colonne `supprime = true`, filtrée à la lecture) ou supprimer **par partition** ; limiter les suppressions ligne par ligne aux cas unitaires ; suivre les fenêtres non vides ; `gc_grace_seconds` plus court **avec** des réparations régulières.
- **Alerte en production** : compter les lignes `Scanned over … tombstones` du journal (seuil d'avertissement `tombstone_warn_threshold`, 1 000 par défaut) et alerter dès qu'elles apparaissent sur une table ; suivre aussi la métrique `TombstoneScannedHistogram` (p99 par table) et le nombre de `ReadFailure` par minute.

---

## Mission 5 — Présence et suggestions

La partie Redis/Neo4j de `load_nosql.py` (fournie) produit :
- Redis : `presence:<id>` = `"en_ligne"` avec expiration de 300 s pour 30 % des utilisateurs (1 500 clés, tirage fixe `Random(7)`), et un ensemble `membres:<serveur>` par serveur (150 ensembles, construits depuis `app.membre`). Les mêmes 1 500 présences sont écrites dans `app.presence` avec `vu_le = now()`.
- Neo4j : un nœud `Utilisateur` par utilisateur, un nœud `Serveur` par serveur (contraintes d'unicité sur `id`), une relation `AMI_DE` par ligne de `app.amitie` (orientée a → b, lue sans direction), une relation `MEMBRE_DE` par ligne de `app.membre`.

### Contrôle de cohérence avant chronométrage

Dans `bench.py`, avant toute mesure : B1 (3 salons, 3 moteurs + correctif), B2b (4 serveurs, après avoir écrit les mêmes 500 présences des deux côtés), B3 et B3 bis (50 utilisateurs tirés au hasard), B4 (3 serveurs, 3 versions). Résultat :
```
«controles»
```

**Pourquoi un benchmark sans ce contrôle ne vaut rien** : un moteur qui renvoie une réponse **fausse** ou **incomplète** (index oublié, données mal chargées, requête qui ne regarde qu'un sens de l'amitié, présence expirée d'un côté seulement) peut être très rapide. On comparerait alors deux travaux différents, et le « gagnant » serait celui qui en fait le moins.

### Mesure 7 — p50 / p95 (ms, n = 300 ; B3 bis : n = 30)

| Cas | PostgreSQL | Redis | Neo4j |
|---|---:|---:|---:|
| B2a battement de cœur (écriture avec expiration) | «b2a_pg» | «b2a_redis» | — |
| B2b membres en ligne, serveur 118 (478 membres) | «b2b_pg» | «b2b_redis» | — |
| B3 serveurs suggérés (≥ 2 amis membres) | «b3_pg» | — | «b3_neo» |
| B3 bis amis d'amis, classés par amis communs | «b3bis_pg» | — | «b3bis_neo» |

Détail de B2b côté Redis (`bench/detail_b2b.py`) :

| Étape | p50 (ms) | p95 (ms) |
|---|---:|---:|
| `SMEMBERS membres:118` seul | «d_smembers_p50» | «d_smembers_p95» |
| `MGET` des 478 présences seul | «d_mget_p50» | «d_mget_p95» |
| Même calcul dans Redis (script Lua, renvoie un nombre) | «d_lua_p50» | «d_lua_p95» |

### Expiration native (démonstration)

```
«expiration»
```

Après 4 secondes, `TTL` vaut −2 et `EXISTS` vaut 0 : la clé n'existe plus, personne ne l'a supprimée.

**Pour avoir la même chose dans PostgreSQL**, il faut ajouter deux choses : (1) que **chaque lecture** filtre `vu_le > now() - interval '5 minutes'` (c'est ce que fait `b2r_pg`) ; (2) une **tâche planifiée** qui supprime les lignes trop vieilles (`DELETE FROM app.presence WHERE vu_le < now() - interval '5 minutes'`, toutes les minutes avec `pg_cron`), sinon la table ne fait que grossir. Et chaque suppression laisse des lignes mortes à nettoyer (`VACUUM`). Rien ne disparaît « tout seul ».

### À décider : chaque moteur a-t-il gagné là où je l'avais prédit ?

- **Redis, écriture (B2a)** : «phrase_b2a»
- **Redis, lecture « qui est en ligne » (B2b)** : «phrase_b2b»
- **Neo4j (B3, B3 bis)** : «phrase_neo»

---

## Mission 6 — Verdict

### Mesure 8 — `python bench.py --n 300` (tableau complet)

«tableau_bench»

Le tableau des hypothèses et la révision des décisions sont dans `DOSSIER_ARCHI.md` (parties 4 et 5).

### Trois limites qui pourraient inverser mes conclusions en production

1. **Le volume.** Tout tient en RAM (46 Mo pour la table des messages et ses index dans PostgreSQL, «mongo_taille» dans MongoDB). Le phénomène qui a fait quitter MongoDB (données plus grosses que la mémoire, lectures disque aléatoires) ne peut pas apparaître. À 30 Go de messages, PostgreSQL aurait le même problème que MongoDB en 2015, et l'avantage de Cassandra (messages d'un salon rangés ensemble, ajout de nœuds) deviendrait réel.
2. **La concurrence.** Un seul client, une requête à la fois. En production : des milliers d'écritures et de lectures en même temps. Ce que je ne vois pas : l'attente sur le verrou de la ligne `serveur` à chaque arrivée (trigger `nb_membres`), le coût du trigger RG7/RG10 sous charge, la mémoire de PostgreSQL avec beaucoup de connexions, les partitions chaudes de Cassandra.
3. **Le réseau et la configuration.** Tout est sur la même machine : un aller-retour coûte une fraction de milliseconde. En vrai réseau, les 26 requêtes successives de Cassandra pour le salon clairsemé, ou les 2 allers-retours de Redis pour B2b, coûtent 26 ou 2 fois la latence réseau. Cassandra tourne sur un seul nœud avec 512 Mo de mémoire Java, sans réplication ni quorum, et Neo4j avec 768 Mo : ce n'est pas la configuration pour laquelle ils sont faits.
(Et en plus : les données sont générées — phrases courtes, aucune suppression, aucune modification en masse, horodatages réguliers.)

### Pourquoi le p95 et pas seulement la moyenne ? Pourquoi un échauffement ?

- **p95** : la moyenne cache les requêtes lentes. Or c'est la requête lente qui se voit : un écran qui fait plusieurs lectures attend la **plus lente**. Si une page déclenche 20 lectures, la probabilité qu'au moins une dépasse le p95 est d'environ 1 − 0,95²⁰ ≈ 64 %. Discord surveille justement le p95 (seuil de 80 ms cité dans le dossier).
- **Échauffement** : les premiers lancements mesurent un démarrage à froid — pages pas encore en mémoire, plans de requête pas encore préparés, connexions qui s'établissent, code Java pas encore optimisé (Cassandra, Neo4j). Exemple mesuré : R1 sur le salon chaud prend «r1c_froid» ms au premier appel, puis «r1c_p50» ms au p50. Sans échauffement, on compare des démarrages, pas le fonctionnement normal.

### À décider : si la direction ne finance que deux moteurs ?

**PostgreSQL + Redis**, comme hier. «phrase_deux_moteurs»

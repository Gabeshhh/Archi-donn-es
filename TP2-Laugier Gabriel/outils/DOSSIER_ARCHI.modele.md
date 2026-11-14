# DOSSIER_ARCHI — version 2 (jour 1 corrigé par les mesures du jour 2)

LAUGIER Gabriel — Bachelor 3 Dev — YNOV Campus Montpellier
Module : Conception et architecture des données (2026-2027)

Ce document reprend mon dossier du jour 1 (PDF) et le corrige avec les mesures du jour 2 (`MESURES.md`). Les parties du jour 1 qui restent vraies ne sont pas recopiées en entier : je renvoie au PDF et je note seulement ce qui change. Chaque phrase est soit un **fait** (mesure ou calcul reproductible, avec la référence « Mesure k »), soit une **hypothèse**, notée [H].

---

## Ce qui change dans la v2 (résumé)

1. **Les messages restent dans PostgreSQL** pour la plateforme miniature. Le critère que je m'étais fixé au jour 1 (décision 1 : « si le p95 des 50 derniers messages dans PostgreSQL reste sous 80 ms, Cassandra ne se justifie pas ») est rempli : p95 = «r1c_p95» ms (Mesure 2). Cassandra reste la cible **à l'échelle de Discord**, avec des signaux de déclenchement chiffrés (partie 4.f).
2. **Neo4j est retiré** : les amis d'amis en SQL prennent «b3bis_pg_p95» ms au p95 (Mesure 7), très loin de mon seuil de 200 ms (décision 3).
3. **MongoDB est retiré** : la pièce jointe devient une colonne JSONB avec un `CHECK` sur son type. Mesuré : MongoDB n'est pas défaillant à ce volume (B1 : «b1_c_mongo_p50» ms au p50, données et index en mémoire), «phrase_resume_mongo»
4. «phrase_resume_redis»
5. **RG3 (propriétaire membre)** est maintenant garantie surtout par des contraintes déclaratives (clé étrangère composée différée + index unique partiel), le trigger ne sert plus qu'au rôle.

---

## 0. Résumé en cinq lignes

Inchangé (voir PDF). J'ajoute une sixième ligne :

6. À l'échelle du TP (300 000 messages, 46 Mo avec index), tout tient en mémoire et PostgreSQL seul répond à toutes les requêtes du TP sous «pg_max_p95» ms au p95 : la décision de Discord en 2015-2017 était juste **pour leur volume**, pas pour le nôtre.

---

## 1. Ce que le système doit garantir

### 1.b Règles de gestion — où chacune est garantie (v2)

| Règle | Jour 1 | v2 (implémenté dans `sql/01_schema.sql`, prouvé dans `sql/04_preuves_contraintes.sql`) |
|---|---|---|
| RG1 e-mail unique | UNIQUE | UNIQUE — refus prouvé |
| RG2 au moins un membre par serveur | non garantie | **garantie** par RG3 : le propriétaire est forcément membre |
| RG3 un propriétaire, membre avec le rôle « propriétaire » | `NOT NULL` + trigger | `NOT NULL` + **FK composée différée** `serveur(id, proprietaire_id) → membre` + **index unique partiel** (un seul rôle propriétaire) + trigger de contrainte différé pour le rôle — 4 refus prouvés |
| RG4 un rôle parmi 3 | PK + CHECK | PK + CHECK — 2 refus prouvés |
| RG5 salon dans un serveur | FK NOT NULL | inchangé |
| RG6 texte/vocal, nom unique | CHECK + UNIQUE | inchangé — 2 refus prouvés |
| RG7 message seulement dans un salon textuel | trigger | trigger sur `message` **+ trigger sur `salon`** (un salon avec messages ne peut pas devenir vocal) — 2 refus prouvés |
| RG8 champs obligatoires | NOT NULL | NOT NULL + CHECK `modifie_le >= envoye_le` — 2 refus prouvés |
| RG9 0 ou 1 pièce jointe, 3 types | table `piece_jointe` | **colonne JSONB** + CHECK sur `type` — refus prouvé |
| RG10 auteur membre du serveur | trigger | trigger (coût mesuré : ≈ «trig_us» µs par message) — refus prouvé |
| RG11 amitié une fois par paire | PK + CHECK a < b | inchangé — 2 refus prouvés |
| RG12 présence qui expire | Redis TTL | Redis TTL (expiration démontrée) ; dans PostgreSQL : filtre à la lecture + nettoyage planifié |
| RG13 suggestions | Neo4j | **requêtes SQL** (B3, B3 bis) |
| RG14 tableau de bord quotidien | table agrégée | **vue matérialisée** rafraîchie la nuit (`CONCURRENTLY`) |

### 1.d MLD v2

Les noms suivent le contrat de l'annexe A du TP (`id`, `serveur_id`…). MCD et MLD : voir `mcd.md`. MPD complet : `sql/01_schema.sql`.

- UTILISATEUR(**id**, pseudo, email, cree_le)
- SERVEUR(**id**, nom, #proprietaire_id, cree_le, nb_membres)
- MEMBRE(**#utilisateur_id, #serveur_id**, role, rejoint_le)
- SALON(**id**, #serveur_id, nom, type, cree_le)
- MESSAGE(**id**, #salon_id, #auteur_id, contenu, envoye_le, modifie_le, pieces_jointes JSONB)
- AMITIE(**#utilisateur_a, #utilisateur_b**, depuis)
- PRESENCE(**#utilisateur_id**, vu_le)

Changement : PIECE_JOINTE disparaît comme table. Raison : l'export fournit un seul objet JSON par message (RG9 : 0 ou 1), il est toujours lu avec son message, et sa forme varie. C'était déjà l'option de ma version minimale (4.e du jour 1).

Index (justifiés en commentaire dans `01_schema.sql`) : `message(salon_id, id)` pour R1/B1 ; `message(auteur_id)` pour la FK ; `membre(serveur_id)` pour la FK et B2b ; `amitie(utilisateur_b)` pour l'autre sens de l'amitié ; index unique partiel sur `membre(serveur_id) WHERE role = 'proprietaire'`. Pas d'index sur `serveur(proprietaire_id)` (150 lignes) ni sur `salon(serveur_id)` (déjà couvert par `UNIQUE (serveur_id, nom)`).

---

## 2. Accès, volumétrie, dénormalisation

### 2.a Latences visées contre latences mesurées

| Accès | Latence visée (jour 1) | Mesuré (p95) | Verdict |
|---|---|---:|---|
| 50 derniers messages d'un salon (R1, avec pseudo) | < 80 ms | «r1c_p95» ms | «v_r1» |
| Droit d'écrire (R2) | < 5 ms | «r2_p95» ms | «v_r2» |
| Battement de cœur | < 20 ms | PG «b2a_pg_p95» ms / Redis «b2a_redis_p95» ms | «v_b2a» |
| Qui est en ligne dans un serveur | < 50 ms | PG «b2b_pg_p95» ms / Redis «b2b_redis_p95» ms | «v_b2b» |
| Suggestions (amis d'amis) | < 1 s | PG «b3bis_pg_p95» ms / Neo4j «b3bis_neo_p95» ms | «v_b3» |
| Tableau de bord | quelques secondes | à la volée «r3_p95» ms / vue «r3v_p95» ms | tenu |

### 2.b Volumétrie — correction

J'avais supposé 300 octets par message [H1]. Dans le TP, une ligne de `message` pèse en moyenne **83 octets** (fait : `avg(pg_column_size(m.*))`), parce que les phrases générées sont très courtes. Je **garde 300 octets pour Discord** (vrais messages, plus longs) : la mesure ne dit rien sur Discord, elle dit seulement que nos données sont plus légères. Taille réelle de la table : 25 Mo, 46 Mo avec ses index (fait).

### 2.c Dénormaliser ou pas — bilan

- **Tableau de bord (vue matérialisée)** : gain ×«f_vue_p50» (Mesure 4). Justifiée pour l'avenir, pas encore par la latence («r3_p50» ms à la volée).
- **nb_membres (trigger)** : prouvée (retour à 478 après insertion puis suppression). Garde-fou : recalcul hebdomadaire.
- **Droit d'écrire : je confirme mon refus de dénormaliser.** R2 prend «r2_p50» ms au p50 sur la source de vérité : un cache n'apporterait rien et ajouterait le risque qu'un membre expulsé puisse encore écrire.

---

## 3. Où vivent les messages

### 3.c La clé de partition — retrouvée dans les mesures

Mon calcul du jour 1 prévoyait que les petits salons lisent plusieurs partitions. Mesuré :

| Salon | Messages | Partitions lues par B1 | Cassandra p50 | avec correctif p50 |
|---|---:|---:|---:|---:|
| chaud (775) | 12 952 | 1 | «b1_c_cs_p50» ms | «b1_c_csf_p50» ms |
| moyen (424) | 90 | 18 | «b1_m_cs_p50» ms | «b1_m_csf_p50» ms |
| clairsemé (342) | 1 | 26 | «b1_s_cs_p50» ms | «b1_s_csf_p50» ms |

C'est **pire que mon calcul** pour le salon clairsemé (j'avais prévu 2 partitions pour 1 000 messages par an) : avec moins de 50 messages au total, la lecture remonte **toujours** jusqu'à la création du salon. Mon idée du jour 1 (« garder la liste des fenêtres non vides ») est implémentée (`b1_cassandra_fenetres`) : 26 → 2 requêtes pour le clairsemé, mais 18 → 18 pour le moyen, dont presque toutes les fenêtres sont non vides. Pour lui, seule une fenêtre plus large aiderait (N variable selon l'activité, idée notée au jour 1).

### 3.d Modes de défaillance — l'incident des tombstones rejoué

Reproduit avec `tombstones.py` (120 000 messages, 119 999 supprimés un par un) : «tomb_resume». Mécanisme, deux correctifs et procédure : `MESURES.md` (Mission 4) et `RUNBOOK_tombstones.md`. Ce que j'ajoute à la conception : pour les suppressions en masse, **masquer puis purger par partition** au lieu de supprimer ligne par ligne, et alerter sur les avertissements de marqueurs dans les journaux. C'est aussi la réponse à ma question 1 au client (« supprimer ou cacher ? ») : à l'échelle Cassandra, **cacher** est le choix le plus sûr.

---

## 4. Architecture : décisions et risques (v2)

### 4.a Carte des besoins v2

| Besoin | Moteur v2 | Jour 1 | Source de vérité ? | Ce qu'on accepte de perdre |
|---|---|---|---|---|
| Comptes, serveurs, membres, amitiés | PostgreSQL | PostgreSQL | oui | le passage à l'échelle horizontal (inutile à 5 000 utilisateurs) |
| Messages | **PostgreSQL** | Cassandra | oui | rien à ce volume |
| Pièces jointes | **PostgreSQL (JSONB)** | MongoDB | oui | rien : une seule pièce jointe par message |
| Droit d'écrire | PostgreSQL | PostgreSQL | oui | «r2_p50» ms par message |
| Présence | Redis | Redis | oui, éphémère | toutes les présences si Redis redémarre (reconstruites en 1 min) |
| Liste des membres par serveur (pour la présence) | Redis (copie) | — | non (copie de `membre`) | quelques secondes de retard |
| Suggestions | **PostgreSQL** | Neo4j | — (requête) | rien |
| Tableau de bord | PostgreSQL (vue matérialisée) | table agrégée | non (calculé) | 24 h de fraîcheur |

Schéma : `architecture.md`.

### 4.c Trois décisions d'architecture — révision

| Décision du jour 1 | Verdict | Mesure qui décide | Ce qui change |
|---|---|---|---|
| **1. Messages dans Cassandra, clé ((salon, fenêtre 10 j), message_id)** | **Modifiée** | R1 p95 = «r1c_p95» ms dans PostgreSQL (Mesure 2) ; 46 Mo de table + index (Mission 0) ; 1 / 18 / 26 partitions lues selon le salon (Mesure 6) | Pour la plateforme miniature, les messages restent dans PostgreSQL. La conception Cassandra (clé, fenêtres, pas de NULL, suivi des fenêtres, procédure tombstones) est **gardée et testée** comme plan de migration, déclenché par les signaux du 4.f. |
| **2. Présence dans Redis, TTL 5 min** | «d2_verdict» | B2a : Redis «b2a_redis_p50» ms contre PG «b2a_pg_p50» ms ; B2b : Redis «b2b_redis_p50» ms contre PG «b2b_pg_p50» ms ; expiration démontrée | «d2_change» |
| **3. PostgreSQL source de vérité, Neo4j en copie** | **Modifiée** : PostgreSQL maintenu, **Neo4j abandonné** | B3 bis p95 = «b3bis_pg_p95» ms en SQL, sous mon seuil de 200 ms ; Neo4j «b3bis_neo_p95» ms (Mesure 7) | Plus de copie des amitiés, donc plus de risque de divergence (mon risque principal du jour 1 disparaît). Neo4j reviendrait si les suggestions passaient à 3 niveaux ou plus. |

### 4.d Le risque principal v2 : les ensembles de membres copiés dans Redis

Le risque du jour 1 (amitiés copiées dans Neo4j) disparaît avec Neo4j. Le nouveau point de copie est `membres:<serveur>` dans Redis, copie de `app.membre`.
- **Si la copie diverge** : un membre expulsé reste compté « en ligne » dans le serveur, ou un nouveau membre n'apparaît pas. C'est visible mais peu grave (pas de fuite de droits : le droit d'écrire est toujours vérifié dans PostgreSQL).
- **Détection** : chaque nuit, comparer `SCARD membres:<s>` à `serveur.nb_membres` pour les 150 serveurs (le contrôle de `bench.py` compare déjà les résultats B2b sur 4 serveurs).
- **Réparation** : reconstruire l'ensemble depuis PostgreSQL (jamais l'inverse).

### 4.e La version minimale (deux moteurs) — confirmée

**PostgreSQL + Redis**, comme au jour 1. Les quatre besoins que j'avais rangés dans PostgreSQL au jour 1 (messages, pièces jointes, suggestions, tableau de bord) sont maintenant **mesurés** et tiennent tous leurs objectifs.

### 4.f Signaux mesurables pour ajouter un moteur

| Signal | Seuil | Testable avec mes mesures ? |
|---|---|---|
| Table `message` + index par rapport à la RAM du serveur PostgreSQL | > 70 % de la RAM | **Oui** : 46 Mo aujourd'hui. Le seuil correspond à environ 100 à 150 millions de messages [H]. |
| p95 de R1 (50 derniers messages) | > 80 ms plusieurs jours de suite | **Oui** : «r1c_p95» ms. Mais seulement sans charge : à surveiller en production. |
| Écritures de messages par seconde en continu | > quelques milliers / s | **Non** : un seul client dans le TP, pas de test de charge. |
| Coût du trigger RG7/RG10 sous charge | si le temps d'écriture d'un message dépasse 50 ms au p95 | **Partiellement** : «trig_us» µs par message mesurés en chargement, pas sous concurrence. |
| p95 des suggestions (amis d'amis) → Neo4j | > 1 s, ou besoin de 3 niveaux | **Oui** pour 2 niveaux : «b3bis_pg_p95» ms. **Non** pour 3 niveaux (pas mesuré). |
| Taille d'une partition Cassandra (si migration) | > 100 Mo | **Oui** (calcul) : la plus grosse fenêtre fait 2 673 messages. |
| Avertissements de tombstones dans les journaux | > 0 lignes `Scanned over ...` | **Oui** (incident reproduit) ; à brancher en alerte. |

---

## 5. Tableau des hypothèses (phase 4.f du jour 1)

| # | Hypothèse | Valeur prédite | Valeur mesurée | Verdict | Explication par un mécanisme |
|---|---|---|---|---|---|
| 1 | PostgreSQL ≈ Cassandra pour les 50 derniers messages du salon chaud | rapport < 2 | PG «b1_c_pg_p50» ms, Cassandra «b1_c_cs_p50» ms (p50) : rapport «h1_rapport» | «h1_verdict» | «h1_explication» |
| 2 | Cassandra : salon clairsemé ≈ 5 fois plus lent que le salon chaud | ×5, « 2 partitions ou plus » | 26 requêtes contre 1 ; «b1_s_cs_p50» ms contre «b1_c_cs_p50» ms : ×«r_s_c_cs» | «h2_verdict» | «h2_explication» |
| 3 | Redis ≈ 5 fois plus rapide que PostgreSQL pour « qui est en ligne » | ×5 plus rapide | Redis «b2b_redis_p50» ms, PG «b2b_pg_p50» ms (p50) | «h3_verdict» | «h3_explication» |
| 4 | Neo4j ≈ 2 fois plus rapide que PostgreSQL pour les amis d'amis | ×2 | PG «b3bis_pg_p50» ms, Neo4j «b3bis_neo_p50» ms (p50) | «h4_verdict» | «h4_explication» |
| 5 | Vue matérialisée ≈ 50 fois plus rapide que le calcul à la volée (R3) | ×50 | «r3_p50» ms → «r3v_p50» ms : ×«f_vue_p50» (p50), ×«f_vue_p95» (p95) | «h5_verdict» | La vue lit 31 lignes déjà calculées par l'index `(serveur_id, jour)` ; le calcul à la volée relit «m3_lus» messages des 4 salons et en jette «m3_jetes_pct» % (filtre sur la date, Mesure 3). Nuance : «r3_p50» ms est déjà acceptable ; et filtrer sur l'id Snowflake au lieu de la date ramène le calcul à «r3sf_p50» ms **sans** dénormaliser. |

---

## 6. Si la direction ne finance que deux moteurs

**PostgreSQL et Redis.** Même réponse qu'hier, mais je la justifie maintenant par des mesures :
- Hier, je gardais PostgreSQL « parce qu'il porte les règles » et je pensais qu'il faudrait vite un moteur pour les messages. Aujourd'hui, j'ai la preuve qu'il tient **toutes** les requêtes du TP sous «pg_max_p95» ms au p95, règles comprises, et je sais à quel signal le quitter pour les messages (4.f).
- «phrase_deux_moteurs_redis»

Ce que j'ai appris : à petite échelle, l'architecture de Discord coûterait plus qu'elle ne rapporte. Elle répondait à un problème de **volume** (données plus grosses que la RAM) que la plateforme miniature n'a pas, et que je sais maintenant détecter avant qu'il arrive.

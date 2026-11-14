# Architecture — version 2 (après mesures)

LAUGIER Gabriel — B3 Dev.

Trait plein = écriture ou lecture directe · pointillés = copie (lot ou événement) · ✂ = retiré par rapport au jour 1.

```mermaid
flowchart TB
    C[Clients web / mobile] -->|HTTPS| API[Application API<br/>vérifie les droits]

    API -->|"comptes, serveurs, membres, amitiés<br/>messages + pièces jointes JSONB<br/>stricte"| PG[(PostgreSQL<br/>source de vérité)]
    API -->|"battement de cœur, TTL 300 s<br/>éphémère"| R[(Redis<br/>présence)]
    API -->|"qui est en ligne ?<br/>script Lua côté Redis"| R

    PG -.->|"ensembles membres:serveur<br/>événement à chaque arrivée / départ<br/>+ resynchro la nuit"| R
    PG -->|"REFRESH CONCURRENTLY chaque nuit<br/>fraîcheur 24 h"| MV[Vue matérialisée<br/>activite_serveur_jour]

    subgraph Plus tard, si un signal se déclenche
        CS[(Cassandra / ScyllaDB<br/>messages par salon + fenêtre)]
    end
    PG -.->|"migration avec dark launch<br/>verif_migration.py"| CS

    X1[✂ Neo4j : suggestions en SQL]:::retire
    X2[✂ MongoDB : pièces jointes en JSONB]:::retire
    classDef retire fill:#eee,stroke:#999,color:#777,stroke-dasharray: 4 3
```

## Ce qui a changé depuis le jour 1, et pourquoi

| Élément | Jour 1 | Version 2 | Mesure qui l'a décidé |
|---|---|---|---|
| Messages | Cassandra | **PostgreSQL** (Cassandra prévue pour plus tard) | R1 p95 très sous le seuil de 80 ms que je m'étais fixé (décision 1, Mesure 2) |
| Pièces jointes | MongoDB | **Colonne JSONB** dans `message` avec `CHECK` sur le type | Aucune requête du TP n'en a eu besoin à part ; le JSONB porte la forme variable |
| Suggestions | Neo4j (copie) | **Requêtes SQL** sur `amitie` et `membre` | B3 bis p95 très sous le seuil de 200 ms de la décision 3 (Mesure 7) |
| Présence | Redis | Redis, **avec un script Lua** pour compter les membres en ligne | B2b (Mesure 7, détail B2b) : le script Lua évite 2 allers-retours et le transfert de 478 ids vers le client |
| Tableau de bord | Table remplie la nuit | Vue matérialisée rafraîchie la nuit | gain mesuré en lecture (Mesure 4), contrôle d'égalité à 0 écart |

Nouveau point de copie à surveiller : les ensembles `membres:<serveur>` dans Redis sont une **copie** de `app.membre`. Contrôle : chaque nuit, comparer `SCARD membres:<s>` à `serveur.nb_membres`, et reconstruire l'ensemble si les deux diffèrent.

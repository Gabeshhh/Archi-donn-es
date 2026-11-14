# MCD — plateforme de discussion (version 2)

LAUGIER Gabriel — B3 Dev. Repris du jour 1. Un seul changement : la pièce jointe n'est plus une entité à part mais une propriété du message à forme variable (0 ou 1 par message, RG9), stockée en JSONB dans PostgreSQL et en sous-document dans MongoDB.

## Entités et associations (notation Merise, cardinalités min,max)

| Association | Côté 1 | Côté 2 | Propriétés |
|---|---|---|---|
| EST MEMBRE | UTILISATEUR (0,n) | SERVEUR (1,n) | role, rejoint_le |
| POSSÈDE | UTILISATEUR (0,n) | SERVEUR (1,1) | — |
| CONTIENT | SERVEUR (0,n) | SALON (1,1) | — |
| PUBLIÉ DANS | SALON (0,n) | MESSAGE (1,1) | — |
| ÉCRIT | UTILISATEUR (0,n) | MESSAGE (1,1) | — |
| EST AMI AVEC (réflexive) | UTILISATEUR (0,n) | UTILISATEUR (0,n) | depuis |
| EST VU | UTILISATEUR (0,1) | PRÉSENCE (1,1) | vu_le (expire après 5 min) |

Entités : UTILISATEUR (id, pseudo, email, cree_le) · SERVEUR (id, nom, cree_le, nb_membres calculé) · SALON (id, nom, type, cree_le) · MESSAGE (id Snowflake, contenu, envoye_le, modifie_le, pieces_jointes à forme variable).

## Même modèle en diagramme entité-association (Mermaid)

```mermaid
erDiagram
    UTILISATEUR ||--o{ MEMBRE : "est membre (0,n)"
    SERVEUR ||--|{ MEMBRE : "a des membres (1,n)"
    UTILISATEUR ||--o{ SERVEUR : "possède (0,n) / 1,1"
    SERVEUR ||--o{ SALON : "contient"
    SALON ||--o{ MESSAGE : "publié dans"
    UTILISATEUR ||--o{ MESSAGE : "écrit"
    UTILISATEUR ||--o{ AMITIE : "utilisateur_a"
    UTILISATEUR ||--o{ AMITIE : "utilisateur_b"
    UTILISATEUR ||--o| PRESENCE : "est vu"

    UTILISATEUR { bigint id PK
                  varchar pseudo
                  varchar email UK
                  timestamptz cree_le }
    SERVEUR { bigint id PK
              varchar nom
              bigint proprietaire_id FK
              timestamptz cree_le
              int nb_membres "dénormalisé" }
    MEMBRE { bigint utilisateur_id PK
             bigint serveur_id PK
             varchar role "proprietaire | moderateur | membre"
             timestamptz rejoint_le }
    SALON { bigint id PK
            bigint serveur_id FK
            varchar nom "unique dans le serveur"
            varchar type "texte | vocal"
            timestamptz cree_le }
    MESSAGE { bigint id PK "Snowflake"
              bigint salon_id FK
              bigint auteur_id FK
              text contenu
              timestamptz envoye_le
              timestamptz modifie_le
              jsonb pieces_jointes }
    AMITIE { bigint utilisateur_a PK
             bigint utilisateur_b PK "a < b"
             timestamptz depuis }
    PRESENCE { bigint utilisateur_id PK
               timestamptz vu_le }
```

## MLD (clé primaire en **gras**, # = clé étrangère)

- UTILISATEUR(**id**, pseudo, email, cree_le)
- SERVEUR(**id**, nom, #proprietaire_id, cree_le, nb_membres)
- MEMBRE(**#utilisateur_id, #serveur_id**, role, rejoint_le)
- SALON(**id**, #serveur_id, nom, type, cree_le)
- MESSAGE(**id**, #salon_id, #auteur_id, contenu, envoye_le, modifie_le, pieces_jointes)
- AMITIE(**#utilisateur_a, #utilisateur_b**, depuis)
- PRESENCE(**#utilisateur_id**, vu_le)
- Vue matérialisée ACTIVITE_SERVEUR_JOUR(**serveur_id, jour**, nb_messages, nb_auteurs)

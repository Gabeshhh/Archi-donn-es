# RUNBOOK — Lectures en échec sur un salon : que fais-je ?

Pour l'exploitant de la table `discord.messages_par_salon` (Cassandra). Une page, à suivre dans l'ordre.

## 1. Symptôme

- Un salon précis ne s'affiche plus (ou très lentement) ; les autres salons marchent.
- Côté application : erreur `ReadFailure` / `code=1300`, avec `READ_TOO_MANY_TOMBSTONES`, ou délai d'attente dépassé.
- Côté nœuds : pauses du ramasse-miettes Java, latence p99 qui monte sur les nœuds qui portent ce salon.

## 2. Diagnostic (5 minutes)

1. Chercher la trace dans les journaux :
   ```bash
   docker compose --profile colonne logs cassandra | grep -i "tombstone"
   ```
   Ligne typique : `Scanned over 100001 tombstones during query ... query aborted`. Elle donne la **clé de partition** `(salon_id, bucket)` en cause. Des lignes `Read ... live rows and ... tombstone cells` (avertissement à partir de 1 000) montrent les partitions qui s'en approchent.
2. Confirmer avec une trace de requête :
   ```sql
   -- cqlsh
   TRACING ON;
   SELECT message_id FROM discord.messages_par_salon WHERE salon_id = <S> AND bucket = <B> LIMIT 50;
   ```
   La trace indique combien de lignes vivantes et de marqueurs ont été lus.
3. Comprendre la cause : quelqu'un a-t-il supprimé beaucoup de messages un par un dans ce salon (modération, robot, suppression de compte) ? Un script a-t-il écrit des `NULL` (chaque NULL écrit est aussi un marqueur) ?

## 3. Action

**Toujours d'abord** : arrêter la source (couper le robot ou le script qui supprime ligne par ligne).

| Situation | Action | Attention |
|---|---|---|
| Toute la fenêtre doit disparaître (purge d'un salon, d'une période) | Supprimer la **partition entière** : `DELETE FROM discord.messages_par_salon WHERE salon_id = <S> AND bucket = <B>;` (un seul marqueur) | Efface tout : réécrire ensuite les messages à garder. |
| Quelques messages doivent rester | Les relire (depuis la source de vérité, ou dans la partition une fois les marqueurs purgés), supprimer la partition, puis les **réécrire** | Faire la relecture **avant** la suppression. |
| Les marqueurs doivent disparaître tout de suite (dernier recours) | Seulement après une **réparation complète** (`nodetool repair discord messages_par_salon`) : `ALTER TABLE discord.messages_par_salon WITH gc_grace_seconds = 0;` puis `nodetool flush` et `nodetool compact discord messages_par_salon` sur chaque nœud, puis **remettre** `gc_grace_seconds = 864000` | Sans réparation, un nœud qui avait raté la suppression peut faire **revenir** les messages supprimés. La compaction complète charge fortement le disque : la lancer hors des heures de pointe, un nœud à la fois. |
| En attendant | L'application peut sauter la fenêtre en cause (lire la fenêtre précédente) pour que le salon redevienne lisible | Le salon semble avoir un « trou » : prévenir le support. |

Vérifier après : relancer la requête de l'étape 2 ; elle doit répondre en quelques millisecondes et la trace ne doit plus montrer de marqueurs.

## 4. Prévention

- **Ne pas supprimer en masse ligne par ligne** : pour une purge, supprimer par partition ; pour une modération en masse, **masquer** (colonne `supprime = true` filtrée à la lecture) puis purger par partition plus tard.
- **Ne jamais écrire de NULL** : omettre la colonne (`UNSET_VALUE`).
- **Suivre les fenêtres non vides** (table `fenetres_par_salon`) pour ne pas parcourir des fenêtres pleines de marqueurs.
- **Réparations régulières**, ce qui permet de réduire `gc_grace_seconds` (par exemple 2 jours au lieu de 10) sans risque de retour des données.
- **Alertes** :
  - toute ligne `Scanned over ... tombstones` dans les journaux → alerte immédiate ;
  - lignes d'avertissement (plus de 1 000 marqueurs lus) → alerte si plus de N par heure sur une même table ;
  - métrique `TombstoneScannedHistogram` (p99 par table) et nombre de `ReadFailure` par minute sur le tableau de bord d'exploitation.

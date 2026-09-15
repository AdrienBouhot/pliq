# Les modèles incrémentaux

Choisir la matérialisation `incremental` ouvre, dans
[l'onglet Sortie](sortie.md), tout ce qu'il faut pour que l'incrémental soit
*correct*.

| Réglage | Ce que ça écrit |
|---|---|
| **Stratégie** | `incremental_strategy='delete+insert'` (ou `append`, `merge`, `microbatch`) |
| **Clé unique** | `unique_key='order_id'` |
| **Colonne de repère** | le bloc `{% if is_incremental() %} where … {% endif %}` |
| **Fenêtre de reprise** | la borne reculée de N jours ou heures, pour les données tardives |
| **Si le schéma change** | `on_schema_change='append_new_columns'` |

Chaque choix est expliqué sur place.

## Les stratégies dépendent de l'entrepôt

Toutes les stratégies n'existent pas partout. L'atelier ne propose que celles
que l'adaptateur du projet connaît, d'après la matrice de la documentation dbt :

| Famille | Stratégies proposées |
|---|---|
| DuckDB | `append`, `delete+insert`, `merge`, `microbatch` |
| PostgreSQL | `append`, `delete+insert`, `microbatch` |
| Redshift | `append`, `delete+insert`, `merge`, `microbatch` |
| Snowflake | `append`, `delete+insert`, `merge`, `microbatch` |
| BigQuery | `merge`, `microbatch` |
| Trino | `append`, `merge` |

Une famille absente de ce tableau n'est pas contrainte : mieux vaut laisser
passer une combinaison qu'on ne connaît pas que refuser une qui marche.

## Ce qui est refusé, et ce qui est seulement signalé

> [!attention] `delete+insert` ou `merge` **sans clé unique** est bloqué à l'enregistrement : ces stratégies remplacent des lignes existantes, et sans clé elles ne savent pas lesquelles.

L'absence de **colonne de repère** est signalée en avertissement, pas bloquée :
le modèle marcherait, mais relirait tout l'historique à chaque exécution.

## La fenêtre de reprise

Elle répond à un silence dangereux.

Une ligne arrivée en retard porte une date *antérieure* au maximum déjà écrit :
un `> max(col)` strict la laisse dehors **définitivement**, et aucun build
n'échoue pour le dire. Reculer la borne de quelques jours la rattrape au passage
suivant.

- Elle est **refusée avec `append`**, où relire ne remplace rien mais duplique.
- Elle s'écrit **famille d'entrepôt par famille**. Sur BigQuery, où la fonction
  dépend du type de la colonne, l'atelier refuse plutôt que de parier.
- Son unité est l'heure ou le jour.

## Si le schéma change

`on_schema_change` dit à dbt quoi faire quand le modèle produit des colonnes que
la table ne contient pas : `append_new_columns`, `sync_all_columns`, `ignore` ou
`fail`. C'est dbt qui applique ; l'atelier ne fait que l'écrire.

## Microbatch

`microbatch` ne se règle pas comme les autres stratégies : voir
[Microbatch](microbatch.md).

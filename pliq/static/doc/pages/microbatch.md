# Microbatch

Avec `microbatch`, c'est **dbt** qui découpe le travail en tranches de temps. Il
refuse de parser le projet tant qu'il ne sait pas sur quelle colonne, depuis
quand, et à quelle taille de tranche.

| Réglage | |
|---|---|
| **`event_time`** | la colonne de temps qui découpe les lignes |
| **`begin`** | la date de départ de l'historique |
| **`batch_size`** | `hour`, `day`, `month` ou `year` |
| **`lookback`** | combien de tranches déjà traitées sont retraitées |

Les trois premiers sont demandés et **validés comme dbt les valide** — les
valeurs de `batch_size` sont celles de dbt, pas un choix de l'atelier.

Aucun filtre `is_incremental()` n'est écrit : dbt borne lui-même, tranche par
tranche.

## Les deux garde-fous que dbt n'a pas

> [!attention] La colonne de temps doit **sortir** du modèle : c'est elle que la matérialisation emploie pour retirer une tranche avant de la réécrire. Un modèle qui la calcule sans la sélectionner passe le parse, et efface de travers.

> [!attention] Une agrégation microbatch doit **regrouper par la colonne de temps**. Sinon un groupe à cheval sur deux tranches donne deux totaux partiels qui cohabitent — sans qu'aucun build n'échoue.

## Quand aucune entrée ne déclare son `event_time`

L'écran le signale : dbt relit alors la source entière pour chaque tranche. Le
modèle est juste, mais le découpage ne fait plus économiser quoi que ce soit —
c'est exactement ce qu'on venait chercher.

# La fraîcheur des sources

Une transformation qui réussit sur des données figées depuis trois semaines
réussit quand même : le vert du [Flow](flow.md) ne dit qu'une moitié de la
vérité.

## Régler la fraîcheur d'une source

Sur une **source**, la fiche [Documenter & tester](documenter.md) règle sa
colonne de chargement et ses seuils.

| Ce qui s'écrit | Où |
|---|---|
| `loaded_at_field` | la table de source, dans son `sources.yml` |
| `freshness: {warn_after, error_after}` | à côté |

Le champ de la colonne de chargement est **libre** plutôt qu'une liste, parce
que dbt veut un *horodatage* là où beaucoup de tables n'ont qu'une date — et
qu'il accepte une expression pour ça :

```yaml
loaded_at_field: "cast(order_date as timestamp)"
```

L'expression est validée avant d'atteindre le fichier.

> [!attention] Un seuil **sans** colonne de chargement est refusé sur les entrepôts qui n'ont pas de métadonnées à lire : dbt y saute la source, et la fraîcheur n'est alors pas bonne, elle est *inconnue* — et rien dans le Flow ne le dirait.

## Mesurer

**source freshness**, dans la barre du haut, lance la mesure (voir
[Construire avec dbt](construire.md)). Chaque source porte ensuite sa propre
pastille sur le Flow, **à part** de celle du dernier run.

Ce ne sont pas la même chose, et c'est pour ça qu'il y en a deux : une source
n'est jamais « construite ». Sa pastille de run ne veut rien dire ; sa pastille
de fraîcheur dit si la donnée est arrivée.

# Faire entrer une table

Une table de départ entre dans le Flow en étant **déclarée**, pas copiée :
l'atelier lit l'entrepôt et montre les tables réellement présentes, en marquant
celles que dbt connaît déjà.

**+ Dataset**, dans la barre du Flow, écrit une entrée dans `sources.yml`. La
table devient alors utilisable dans une recipe, et dans du SQL, via
`{{ source('raw', 'orders') }}`.

| | Ce que ça écrit | Quand |
|---|---|---|
| **+ Dataset** | une entrée dans `sources.yml` | la table est dans l'entrepôt, chargée par un autre outil (Fivetran, Airbyte, un script) |
| *(une recipe)* | `models/…/<nom>.sql` | c'est vous qui la calculez |

## Charger des données n'est pas le travail de l'atelier

L'entrepôt est alimenté par ailleurs, dbt transforme ce qui s'y trouve. C'est le
partage de dbt, et Pliq ne le déplace pas.

Un **seed** déjà présent dans le dépôt reste affiché, documenté et exécuté
normalement (`dbt seed` est dans la barre du haut) ; l'atelier n'en crée
simplement pas.

## Ce qu'une source gagne à être déclarée

Une fois la table déclarée, elle n'est pas seulement visible :

- elle peut être **documentée et testée** comme n'importe quel dataset, voir
  [Documenter](documenter.md) ;
- elle peut porter une **fraîcheur** — colonne de chargement et seuils — que
  `dbt source freshness` mesure, voir [La fraîcheur des sources](fraicheur.md) ;
- elle devient un nœud du graphe, donc un point de départ pour
  [créer une recipe](recipes.md).

> [!attention] Une source n'est jamais « construite » : dbt ne la calcule pas, il la lit. Sa pastille de fraîcheur dit donc autre chose que la pastille de run d'un modèle, et les deux sont affichées séparément sur le Flow.

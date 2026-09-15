# L'onglet Sortie

Le deuxième onglet d'une recipe : **comment** le modèle est écrit dans
l'entrepôt.

| Réglage | Ce que ça écrit |
|---|---|
| **Description** | la description du modèle, qui ressort dans `dbt docs` |
| **Matérialisation** | `view`, `table`, `incremental`, `ephemeral` |
| **Tags** | `tags=['finance']` — des sélecteurs pour `dbt build --select tag:finance` |

## Les quatre matérialisations

| | Quand |
|---|---|
| `view` | le calcul est léger et doit toujours refléter l'amont — la couche `staging`, le plus souvent |
| `table` | le calcul est coûteux et lu souvent — un mart |
| `incremental` | la table est grande et ne change qu'à la marge — voir [Les modèles incrémentaux](incremental.md) |
| `ephemeral` | le modèle n'existe que comme CTE dans ceux qui le lisent ; rien n'est écrit dans l'entrepôt |

À la création d'une recipe, la matérialisation suit ce que `dbt_project.yml`
impose à la couche choisie (`staging` → `view`, `marts` → `table`). C'est une
proposition, elle se change ici.

## Ce que l'atelier refuse

Certaines configurations produiraient un modèle **faux**, pas un modèle qui
échoue. L'atelier les bloque à l'enregistrement plutôt que de les laisser
passer : c'est le sujet de la page [Les modèles incrémentaux](incremental.md).

> [!note] L'aperçu d'un modèle incrémental montre toujours le **rafraîchissement complet** : le bloc `is_incremental()` est retiré, exactement comme dbt le fait au premier build.

# Ce qui est écrit sur le disque

Une recipe produit **deux fichiers** :

```
models/staging/orders_prepared.sql     ← le modèle dbt, lisible et relisible
.pliq/recipes/orders_prepared.yml      ← le script visuel
```

## Le `.sql` est la source de vérité

Chaque étape du script y devient un CTE nommé et commenté — par la même phrase
que la carte de l'étape affiche :

```sql
-- Recipe « Préparer » de l'atelier Pliq.
-- Script visuel : .pliq/recipes/orders_prepared.yml
-- Ce fichier reste la source de vérité : il s'édite aussi à la main.

with stg_orders as (
    select * from {{ ref('stg_orders') }}
),

-- 1 · Mettre status en majuscules
texte as (
    select ..., upper(cast(status as varchar)) as status, ...
    from stg_orders
),

-- 2 · Calculer amount_ttc = round(amount_eur * 1.2, 2)
formule as (
    select ..., round(amount_eur * 1.2, 2) as amount_ttc
    from texte
)

select * from formule
```

C'est un modèle dbt ordinaire : il se lit en revue de code, se construit en CI,
et survit à l'atelier.

## Le `.yml` de recipe vit hors des `model-paths`

Il est dans `.pliq/`, que dbt ne lit jamais. **Le perdre ne coûte que la
décomposition visuelle** — le modèle, lui, continue de tourner, et s'affiche
alors comme une [recipe SQL](sql.md).

> [!astuce] Versionnez `.pliq/` avec le reste : c'est ce qui permet à quelqu'un d'autre de rouvrir la recipe dans l'atelier plutôt que de relire le SQL. Ce n'est pas une obligation — le projet dbt est complet sans lui.

## Les autres fichiers touchés

| Fichier | Quand |
|---|---|
| `schema.yml` voisin du modèle | descriptions, tags et tests — à l'endroit que le `patch_path` désigne |
| `sources.yml` | déclaration d'une table de source, sa fraîcheur, sa documentation |
| `seeds/schema.yml` | documentation et tests d'un seed |
| `~/.pliq/projects.json` | la liste des projets connus — le seul fichier écrit hors de vos projets |

Le pliage du Flow, lui, est mémorisé **dans votre navigateur** : c'est un
confort d'affichage, il n'a rien à faire dans le dépôt.

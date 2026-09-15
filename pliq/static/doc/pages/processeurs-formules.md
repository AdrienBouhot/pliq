# Processeurs — Formules

Deux processeurs qui **créent** une colonne à partir d'une expression.

## Formule

Crée une colonne à partir d'une expression SQL.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne de sortie** | le nom de la colonne créée |
| **Expression SQL** | l'expression, écrite telle quelle |

```sql
amount_eur * 1.2
upper(country)
datediff('day', ordered_at, delivered_at)
```

> [!attention] L'expression est du SQL de **votre** entrepôt, écrit tel quel : l'atelier ne la traduit pas d'un entrepôt à l'autre. Une recipe qui emploie `datediff` ne se rejouera pas telle quelle sur un entrepôt qui ne connaît pas cette fonction.

Ce qui est vérifié avant l'écriture : que l'expression est **syntaxiquement
valide** pour le dialecte du projet, avec `sqlglot`. Une formule invalide est
refusée avec un message, et non au moment du `dbt build`.

## Si, alors, sinon

Crée une colonne conditionnelle. C'est un `case when` écrit par l'atelier.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne de sortie** | le nom de la colonne créée |
| **Si (condition SQL)** | la condition |
| **Alors** | la valeur si la condition est vraie |
| **Sinon** | la valeur sinon |

La carte d'étape affiche « Calculer *colonne* par condition » : la condition
elle-même se lit dans le `.sql`, où le `case when` est écrit en clair sous le
commentaire de l'étape.

> [!astuce] Pour plusieurs branches, empilez plusieurs étapes « Si, alors, sinon » — chacune devient un CTE, et chacune se prévisualise à son rang avec l'œil. C'est plus long à écrire qu'un `case` à cinq branches, et beaucoup plus simple à déboguer.

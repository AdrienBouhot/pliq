# Processeurs — Filtrage

Trois processeurs qui décident **quelles lignes** traversent l'étape.

## Filtrer sur une valeur

Garde, retire ou vide les lignes selon une valeur.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qu'on regarde |
| **Opérateur** | voir le tableau ci-dessous |
| **Valeurs** | une ou plusieurs, selon l'opérateur |
| **Action** | Retirer les lignes, Garder les lignes, ou Vider la cellule |

### Les opérateurs

| Opérateur | Se lit | Valeurs attendues |
|---|---|---|
| `eq` | vaut | une |
| `ne` | ne vaut pas | une |
| `in` | est dans | plusieurs |
| `not_in` | n'est pas dans | plusieurs |
| `contains` | contient | une |
| `starts_with` | commence par | une |
| `ends_with` | finit par | une |
| `gt` `gte` | `>` `≥` | une |
| `lt` `lte` | `<` `≤` | une |
| `empty` | est vide | aucune |
| `not_empty` | n'est pas vide | aucune |

L'écran n'affiche que les champs que l'opérateur emploie, et le compilateur
refuse une seconde valeur donnée à un opérateur qui n'en compare qu'une —
plutôt que de la jeter en silence.

> [!astuce] **Vider la cellule** est la troisième action, et la plus oubliée : elle garde la ligne et met la valeur à `NULL`. C'est ce qu'on veut pour neutraliser une valeur sentinelle (`-1`, `9999`, `N/A`) sans perdre la ligne.

## Filtrer avec une formule

Garde ou retire les lignes selon une condition SQL.

| Champ | Ce qu'on y met |
|---|---|
| **Condition SQL** | l'expression booléenne |
| **Action** | Garder les lignes, ou Retirer les lignes |

La condition est du SQL de votre entrepôt, validée syntaxiquement avant
l'écriture — même règle que pour une [formule](processeurs-formules.md#formule).

## Supprimer les lignes vides

Retire les lignes où une colonne est vide.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qui doit être remplie |

C'est le raccourci de « Filtrer sur une valeur » avec l'opérateur `empty` et
l'action « Retirer les lignes » — écrit en une étape, et lisible comme une
intention.

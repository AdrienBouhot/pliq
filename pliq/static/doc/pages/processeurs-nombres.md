# Processeurs — Nombres

## Arrondir

Arrondit une colonne numérique.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qu'on arrondit |
| **Décimales** | le nombre de décimales conservées |

`0` décimale arrondit à l'entier. La carte d'étape affiche « Arrondir *colonne*
à *n* décimale(s) », et le `.sql` écrit un `round` du dialecte de l'entrepôt.

> [!astuce] Arrondir une mesure **avant** de l'agréger n'est pas la même chose qu'arrondir le total : la somme de valeurs arrondies dérive. Si le total doit tomber juste, arrondissez après l'agrégation — donc dans une recipe [Préparer](preparer.md) posée derrière la recipe [Grouper](grouper.md).

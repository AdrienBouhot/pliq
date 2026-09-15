# L'écran d'une recipe

L'écran central de l'atelier. À gauche un panneau à trois onglets, à droite la
**grille**, qui se recalcule en continu.

| Onglet | Ce qu'on y règle |
|---|---|
| **Script** | ce que la recipe fait — les étapes, les jointures, les mesures |
| **Sortie** | comment le modèle est écrit dans l'entrepôt — voir [L'onglet Sortie](sortie.md) |
| **Tests** | ce qui doit rester vrai — voir [Les tests](tests.md) |

## La grille

La grille montre le résultat de la recipe sur un **échantillon** (200 lignes par
défaut). Les colonnes touchées par l'étape prévisualisée sont **surlignées en
vert pâle** ; une colonne supprimée par une étape reste affichée, barrée, à sa
place — pour qu'on voie ce qu'on a retiré.

Cliquer l'en-tête d'une colonne ouvre le menu des étapes pertinentes pour
*cette* colonne. C'est le chemin le plus court vers une transformation : voir
[La bibliothèque de processeurs](processeurs.md).

## L'aperçu a un coût, et il l'annonce

Le nombre de lignes ne borne que ce qui redescend : la requête, elle, s'exécute
en entier dans l'entrepôt. Sur un entrepôt facturé à la requête, taper dans un
champ se paie.

- la **durée** de la dernière requête est affichée à côté de la grille ;
- **aperçu manuel** coupe le recalcul automatique : plus rien ne part tant qu'on
  ne clique pas **Recalculer**, et la grille dit « le script a changé depuis » ;
- **Contrôle complet** agrège sur la table entière, et compare l'entrée à la
  sortie : « 120 lignes supprimées (12 %) », « 340 valeurs de `date_livraison`
  sont devenues vides ». Deux phrases qu'un échantillon ne peut pas produire.

> [!note] L'aperçu **n'écrit rien**. Le script est compilé, puis envoyé tel quel à `dbt show --inline` : c'est dbt qui résout `ref()`, `source()`, vos macros et vos `var()`, avec le manifest du projet. Aucun fichier n'est créé tant que vous n'enregistrez pas.

## Annuler, rétablir

**⌘Z / Ctrl+Z** annule, **⇧⌘Z / Ctrl+Y** rétablit — deux boutons dans l'en-tête
du script font la même chose. Dans un champ de saisie, ⌘Z appartient au champ :
c'est le texte en cours qu'on annule, pas l'étape.

L'historique vit dans l'onglet et meurt avec lui. C'est délibéré : le `.sql` sur
le disque reste la seule vérité, et un brouillon caché dans le navigateur en
installerait une seconde, invisible et vieillissante. Revenir exactement sur
l'état enregistré rend le bandeau **ENREGISTRÉ**.

## Ce que l'enregistrement vérifie avant d'écrire

Si des colonnes **disparaissent** et que des modèles en aval les lisent,
l'atelier le dit avant d'écrire, avec la liste. Sans ça, on l'apprend au
`dbt build` suivant, une fois le fichier déjà remplacé.

Seuls les modèles qui *lisent* la colonne sont listés : un `select *` ne casse
pas, sa sortie change simplement de forme. Et quand la colonne ne disparaît pas
mais **change de nom**, l'alerte le dit ainsi : « renommée en `total` » plutôt
que « supprimée ». Les deux ne se réparent pas pareil.

> [!attention] L'atelier avertit, il ne répare pas : renommer une colonne ne réécrit aucun modèle d'aval, il faut les ouvrir. Il a su le faire, et ne le fait plus — la réécriture était textuelle, donc aveugle à la portée SQL, et les deux branches d'un `union all` n'ont pas la même. Un renommage silencieusement faux coûte plus cher que pas de renommage.

L'alerte est volontairement large : un nom dans une chaîne de caractères —
l'`except` d'un `dbt_utils.star` — casse le build aussi, et il est signalé.

## Enregistrer

- **Enregistrer** écrit les fichiers : le `.sql`, le script visuel, et ce que
  l'onglet Tests met dans le `schema.yml` voisin.
- **Enregistrer & exécuter** fait la même chose, puis lance `dbt build` sur le
  modèle produit.

Pendant un `dbt build`, dbt est occupé : il n'accepte qu'une invocation à la
fois. Une écriture est alors refusée **avant** de toucher au disque — dbt lit
ces fichiers-là pendant qu'il construit — et une lecture est refusée tout de
suite plutôt que d'attendre en silence.

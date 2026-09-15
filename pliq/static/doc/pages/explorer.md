# Explorer un dataset

**Explorer**, dans le panneau d'un dataset, ouvre la grille de données avec,
pour chaque colonne, quatre bandeaux :

1. le **nom** (cliquable) ;
2. le **type de stockage** de l'entrepôt (`varchar`, `bigint`, `date`…) ;
3. la **signification** devinée à partir des valeurs (Email, Date, Code pays…) ;
4. la **barre de qualité** : vert = valide, gris = vide, rouge = invalide pour
   cette signification.

## L'échantillon, et le contrôle complet

Tout cela porte sur l'**échantillon** — 200 lignes par défaut.

**Contrôle complet** agrège sur la table entière : lignes, remplies, vides,
distinctes, minimum, maximum. Et il le dit en toutes lettres.

> [!attention] Deux cents lignes ne savent pas combien de valeurs distinctes compte une table d'un million, et prendre l'un pour l'autre est la façon la plus simple de se tromper d'un facteur mille. C'est pour cela que les deux mesures ne sont jamais présentées comme équivalentes.

Le contrôle complet s'arrête à **soixante colonnes** : au-delà, la requête
d'agrégation devient elle-même un problème. Les colonnes écartées sont nommées,
et l'atelier distingue ce motif d'un type qu'il ne sait pas agréger.

## Ce que l'exploration coûte

La lecture passe par `dbt show`, donc par l'adaptateur du projet : c'est une
requête sur votre entrepôt. Le nombre de lignes ne borne que ce qui redescend —
la requête, elle, s'exécute en entier. Sur un entrepôt facturé à la requête, ça
compte ; la même mise en garde vaut pour l'aperçu d'une recipe, voir
[L'écran d'une recipe](recipe-ecran.md#l-apercu-a-un-cout-et-il-l-annonce).

## Les types affichés

Les types de l'aperçu sont ceux qu'agate rend à dbt (`INTEGER`, `VARCHAR`,
`DECIMAL`, `DATE`…), plus grossiers que les types déclarés de l'entrepôt :
`int` et `bigint` s'y confondent. Le bandeau « type de stockage », lui, vient
de l'entrepôt.

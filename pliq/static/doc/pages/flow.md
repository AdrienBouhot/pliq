# Lire le Flow

Le Flow est l'écran d'accueil d'un projet ouvert : le graphe **biparti** de
tout ce qu'il contient, comme dans un ETL visuel classique. Les datasets et les
recipes y alternent — `dataset → recipe → dataset` — et une recipe est un nœud
à part entière, pas une flèche.

## Les formes et les couleurs

| Forme | Ce que c'est |
|---|---|
| **Carré bleu** | un dataset : seed, source, ou modèle dbt |
| **Cercle orange** | une recipe visuelle, avec son icône (balai = Préparer, deux anneaux = Joindre…) et un badge donnant le nombre d'étapes |
| **Cercle orange foncé** | une recipe SQL, c'est-à-dire un modèle écrit à la main |
| **Bloc** | un repli : plusieurs datasets contractés en un seul nœud — voir [Quand il y a trop de tables](flow-grands-projets.md) |

La **pastille** d'un dataset indique le statut du dernier run. Une **source** en
porte une seconde, en haut : sa [fraîcheur](fraicheur.md). Ce ne sont pas la
même chose, et une source n'est jamais « construite ».

Une recipe SQL affiche malgré tout un type deviné — jointure, agrégat, union :
l'atelier lit son SQL pour le proposer. C'est une lecture, pas une
décomposition ; voir [Recipe SQL](sql.md).

## Le panneau de droite

**Cliquez un dataset** : le panneau propose
[**Explorer**](explorer.md), **Construire**,
[**Documenter & tester**](documenter.md), [**Renommer**](renommer.md), et la
grille des recipes qu'on peut créer à partir de lui — c'est le chemin normal
pour [créer une recipe](recipes.md).

**Cliquez une recipe** : il propose **Ouvrir**, **Construire** et **Supprimer**.

Le panneau montre aussi les tests portés par le dataset. **Cliquer un test**
montre les lignes qui le font échouer — voir [Les tests](tests.md#les-lignes-en-echec).

## Le clic droit

Le clic droit ouvre le menu du nœud :

- sur un **dataset** : le renommer, puis les replis (amont, aval, dossier,
  seeds, sources) ;
- sur une **recipe** : ce qu'on en fait — l'ouvrir, la construire, explorer son
  résultat, la supprimer.

## La suite

- [Se déplacer dans le flux](flow-naviguer.md) — les gestes, le zoom, le cadrage.
- [Quand il y a trop de tables](flow-grands-projets.md) — replier, suivre une
  lignée, chercher.
- [Faire entrer une table](datasets.md) — déclarer une table de l'entrepôt.

# Supprimer une recipe

**Clic droit sur la recipe → Supprimer**, ou le bouton **Supprimer** du panneau
de droite et de l'en-tête de l'éditeur.

## Deux fichiers portent le même nom

La modale les sépare :

| | Ce qui est effacé |
|---|---|
| Par défaut | le script visuel `.pliq/recipes/<nom>.yml`, le modèle `<nom>.sql`, et ce que le `schema.yml` voisin en disait |
| Case décochée | le script visuel seulement — le modèle reste dans le Flow, devenu **écrit à la main** |

La seconde option est la bonne quand on veut garder le résultat et cesser de le
maintenir visuellement : le `.sql` reste, et devient une
[recipe SQL](sql.md) ordinaire.

## Ce que la modale compte

Elle nomme les fichiers, et **compte ce qui est calculé à partir du modèle** :
supprimer un dataset dont d'autres dépendent les empêche de se construire,
autant le savoir avant.

Un modèle écrit à la main n'a pas de script visuel : le supprimer, c'est effacer
son `.sql`, et la modale ne propose donc pas le choix.

> [!attention] Rien n'est déposé dans une corbeille — c'est irréversible. Si le projet est versionné, git reste le filet ; l'atelier n'en a pas d'autre.

# Les projets dbt

Un projet dbt, c'est un dossier contenant un `dbt_project.yml`, rien d'autre.
L'accueil les liste avec ce qu'il faut pour les reconnaître : adaptateur, cible,
nombre de modèles et de seeds, taille de la base, date du dernier build.

## Les quatre gestes de l'accueil

| | Ce que ça fait |
|---|---|
| **+ Nouveau projet** | écrit un projet DuckDB complet et l'ouvre |
| **Ajouter un dossier existant** | fouille un dossier sur trois niveaux et retient les projets trouvés |
| **Retirer** | sort le projet de la liste — aucun fichier n'est touché |
| **Supprimer** | efface le dossier du disque, et exige qu'on tape son nom |

### + Nouveau projet

Le projet écrit est complet et immédiatement constructible : `dbt_project.yml`,
`profiles.yml` (cibles `dev` et `prod`), les trois couches `staging` /
`intermediate` / `marts`, et les dossiers `seeds`, `macros`, `tests`,
`snapshots`, `analyses`.

> [!note] L'atelier ne sait **créer** que des projets DuckDB. Pour un autre entrepôt : `dbt init`, puis **Ajouter un dossier existant**.

### Ajouter un dossier existant

L'atelier descend de trois niveaux et s'arrête au premier `dbt_project.yml`.
C'est le chemin normal pour un projet créé avec `dbt init`, ou pour un dépôt
cloné qui contient plusieurs projets.

### Supprimer

**Supprimer** efface le dossier du disque, et demande qu'on tape son nom pour
le confirmer. Le projet ouvert ne peut pas être supprimé. Rien n'est déposé
dans une corbeille.

## Changer de projet

Changer de projet **ne redémarre rien** : l'atelier lâche l'adaptateur du projet
précédent, change de répertoire courant, et reparse. C'est aussi à ce moment
que les connexions dbt sont réinitialisées — un entrepôt sur fichier n'accepte
souvent qu'un seul écrivain.

## Le seul fichier écrit hors de vos projets

La liste des projets connus vit dans `~/.pliq/projects.json`. C'est le seul
fichier que l'atelier écrit en dehors de vos projets dbt : tout le reste — les
modèles, les scripts visuels, la documentation, les tests — vit dans le projet
et se versionne avec lui. Voir [Ce qui est écrit sur le disque](fichiers.md).

Le pliage du Flow, lui, est mémorisé par projet **dans votre navigateur** :
c'est un confort d'affichage, il ne mérite pas d'entrer dans le dépôt.

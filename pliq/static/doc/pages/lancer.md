# Lancer l'atelier

```bash
cd mon_projet_dbt
pliq                          # ouvre http://127.0.0.1:8765

pliq --no-browser
pliq --target prod
pliq --project-dir ../autre_projet
pliq -v                       # journal détaillé
```

Le projet dbt est détecté automatiquement : un `dbt_project.yml` dans le dossier
courant, ou dans un sous-dossier direct. Pour arrêter l'atelier : `Ctrl-C` dans
le terminal qui l'a lancé.

## Les options

| Option | Ce qu'elle fait |
|---|---|
| `--project-dir` | ouvrir un projet ailleurs que dans le dossier courant |
| `--target` | la cible dbt à employer (`dev`, `prod`… telle que `profiles.yml` la nomme) |
| `--profiles-dir` | le dossier qui contient `profiles.yml`, quand il n'est pas là où dbt le cherche |
| `--no-browser` | ne pas ouvrir le navigateur au démarrage |
| `--port` | écouter ailleurs que sur 8765 |
| `--host`, `--exposer` | sortir de la boucle locale — voir [Sur le réseau](reseau.md) |
| `-v` | un journal détaillé, utile quand dbt refuse quelque chose |

## Le répertoire courant

L'atelier se place dans le dossier du projet dbt au démarrage, et non seulement
`--project-dir`. Ce n'est pas un détail : un adaptateur sur fichier (DuckDB,
SQLite) résout un `path:` relatif depuis le répertoire courant du processus.
Sans ce déplacement, l'atelier écrirait dans une base et lirait dans une autre.

## Un projet qui déclare des paquets

Un projet qui déclare des paquets dbt ne parse pas tant qu'ils ne sont pas
installés. L'atelier le signale par une pastille **dépendances à installer**
dans la barre du haut, et l'installation part d'un clic — plutôt que de laisser
lire un message brut de dbt qui ne dit pas quoi faire. Voir
[Construire avec dbt](construire.md#dbt-deps).

> [!astuce] `pliq` sans projet dbt sous la main ouvre quand même l'accueil : de là, **+ Nouveau projet** écrit un projet DuckDB complet et immédiatement constructible. Voir [Les projets dbt](projets.md).

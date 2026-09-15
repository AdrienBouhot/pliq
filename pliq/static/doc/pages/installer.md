# Installer

Pliq ne dépend que de **dbt Core**. L'entrepôt est l'affaire de dbt, via
l'adaptateur que votre projet a choisi : Pliq n'en impose aucun, exactement
comme `dbt-core`, qui n'embarque aucun adaptateur.

## Dans le venv du projet dbt

Le plus simple, et celui qui pose le moins de questions : Pliq se sert du dbt
et de l'adaptateur déjà en place, et respecte donc le pin de votre équipe.

```bash
pip install pliq
cd mon_projet_dbt && pliq
```

## En outil isolé

Avec l'adaptateur injecté à côté :

```bash
uv tool install pliq --with dbt-snowflake   # ou dbt-duckdb, dbt-bigquery…
uvx --with dbt-duckdb pliq                  # essayer sans rien installer
```

> [!attention] Un outil isolé n'a pas la version de dbt de votre équipe. Si le projet est pinné sur une version précise de `dbt-core`, installez plutôt Pliq dans son venv : c'est le seul moyen de compiler exactement comme la CI.

## Ce qui n'est pas installé

Aucun driver d'entrepôt n'est installé par Pliq, et aucun n'est importé : toute
lecture passe par `dbt show`, donc par l'adaptateur de votre projet. L'aperçu
marche partout où dbt marche.

## Ce qu'il faut à côté

| | |
|---|---|
| Python | 3.10 ou plus |
| dbt Core | 1.9 ou plus, avec un adaptateur |
| Un projet dbt | un dossier contenant un `dbt_project.yml` — ou aucun, Pliq sait en créer un |
| Un navigateur | l'atelier s'ouvre sur `http://127.0.0.1:8765` |

Pliq n'a pas besoin de Node, ni d'une base à lui : l'interface est faite de
modules ES que le navigateur résout lui-même, et la seule chose que l'atelier
écrit hors de vos projets est la liste des projets connus,
`~/.pliq/projects.json`.

## La suite

[Lancer l'atelier](lancer.md) — les options de démarrage, et comment le projet
est détecté.

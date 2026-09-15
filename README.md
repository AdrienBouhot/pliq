<p align="center">
  <img src="https://raw.githubusercontent.com/AdrienBouhot/pliq/main/pliq/static/logo.png" alt="Pliq" width="64">
</p>

<h1 align="center">Pliq</h1>

<p align="center">
  <a href="https://pypi.org/project/pliq/"><img src="https://img.shields.io/pypi/v/pliq" alt="PyPI"></a>
  <a href="https://pypi.org/project/pliq/"><img src="https://img.shields.io/pypi/pyversions/pliq" alt="Python"></a>
  <a href="https://github.com/AdrienBouhot/pliq/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/pliq" alt="MIT"></a>
</p>


Atelier **ETL visuel** posé sur **dbt Core**. La grammaire est celle d'un ETL visuel :
`dataset → recipe → dataset`, où une recipe est un nœud à part entière et se
configure dans une grille de données, pas dans un éditeur de texte.

Ce que vous construisez à la souris **est** un projet dbt : des fichiers `.sql`
et `.yml` normaux, versionnés, que `dbt build` exécute en CI sans l'atelier.

La documentation complète s'ouvre depuis l'atelier — le **?** de la barre du
haut — ou se lit dans le dépôt :
[`pliq/static/doc/pages/`](https://github.com/AdrienBouhot/pliq/blob/main/pliq/static/doc/pages/index.md).

## Installer

Pliq ne dépend que de **dbt Core**. L'entrepôt est l'affaire de dbt, via
l'adaptateur que votre projet a choisi — Pliq n'en impose aucun, exactement
comme `dbt-core` qui n'embarque aucun adaptateur.

Le plus simple : dans le venv du projet dbt. Pliq se sert du dbt et de
l'adaptateur déjà en place, et respecte donc le pin de votre équipe.

```bash
pip install pliq
cd mon_projet_dbt && pliq
```

En outil isolé, avec l'adaptateur injecté à côté :

```bash
uv tool install pliq --with dbt-snowflake   # ou dbt-duckdb, dbt-bigquery…
uvx --with dbt-duckdb pliq                  # essayer sans rien installer
```

Aucun driver d'entrepôt n'est installé par Pliq, et aucun n'est importé :
toute lecture passe par `dbt show`, donc par l'adaptateur de votre projet.
L'aperçu marche partout où dbt marche.

## Lancer

```bash
cd mon_projet_dbt
pliq                          # ouvre http://127.0.0.1:8765

pliq --no-browser
pliq --target prod
pliq --project-dir ../autre_projet
pliq -v                       # journal détaillé
```

Le projet dbt est détecté automatiquement (`dbt_project.yml` dans le dossier
courant ou un sous-dossier direct). Arrêter : `Ctrl-C`.

### Sur le réseau — ne le faites pas sans le vouloir

L'atelier écoute sur `127.0.0.1` et **n'a aucune authentification**. C'est un
outil de poste de travail : il compte sur le fait d'être joignable depuis votre
seule machine, et rien d'autre ne l'oppose à qui atteint le port.

Ce que peut faire quiconque l'atteint :

- exécuter du SQL et du Jinja dbt quelconques sur votre entrepôt — c'est ce que
  fait une recipe « SQL », et son aperçu ;
- écrire, renommer et supprimer les fichiers de vos projets dbt ;
- supprimer un dossier de projet entier ;
- lire vos profils dbt tels que dbt les résout.

Les parades que l'atelier porte — contrôle du nom d'hôte, contrôle de
l'origine sur le WebSocket, refus d'un corps JSON sans `Content-Type` — visent
une seule attaque : une page web qui essaierait de parler à un atelier *local*
depuis votre navigateur. Aucune ne protège du réseau.

`--host` hors boucle locale exige donc `--exposer`, qui dit que vous l'assumez.
Le plus souvent, ce n'est pas ce qu'il faut : pour joindre un atelier distant,
laissez-le sur `127.0.0.1` et passez par un tunnel.

```bash
ssh -L 8765:127.0.0.1:8765 la-machine   # puis http://127.0.0.1:8765 chez vous
```

## La documentation

Le **?** de la barre du haut ouvre la documentation, servie par l'atelier
lui-même sur `/doc` : les écrans un par un, les vingt-deux processeurs classés
comme dans la bibliothèque, l'incrémental et ses garde-fous, ce qui est écrit
sur le disque, et les limites assumées.

Elle ne demande rien au réseau — pages Markdown rendues sur place, mêmes
polices et même palette que l'atelier — et vit dans le dépôt :

```
pliq/static/doc/pages/*.md      le contenu, une page par sujet
pliq/static/doc/sommaire.json   l'ordre des chapitres
```

Ajouter une page, c'est écrire un `.md` et une ligne dans le sommaire. Les
tests (`tests/test_doc.py`) refusent un lien mort, une ancre disparue, une page
hors sommaire, et un processeur ajouté sans sa section.

## Ce qu'une recipe écrit sur le disque

```
models/staging/orders_prepared.sql     ← le modèle dbt, lisible et relisible
.pliq/recipes/orders_prepared.yml      ← le script visuel
```

Le `.sql` est **la source de vérité** : chaque étape y devient un CTE nommé et
commenté. Le `.yml` vit dans `.pliq/`, hors des `model-paths` — dbt ne le lit
jamais, et le perdre ne coûte que la décomposition visuelle. Le détail :
[« Ce qui est écrit sur le disque »](https://github.com/AdrienBouhot/pliq/blob/main/pliq/static/doc/pages/fichiers.md).

## Architecture

```
pliq/            le paquet publié sur PyPI
  config.py       découverte du projet, profiles.yml, cible active
  projects.py     registre des projets, fiche d'un projet, création, suppression
  datasets.py     inventaire de l'entrepôt, sources, couche suggérée
  dbt_service.py  dbtRunner (API programmatique) + streaming des événements
  graph.py        manifest → Flow biparti dataset/recipe, positions et zones repliées
  join_diagnostics.py  ce qu'une jointure fait aux lignes, sur les tables entières
  full_check.py   profilage agrégé sur toute la table (et non sur l'échantillon)
  profiling.py    types de stockage, significations, barres de qualité
  warehouse.py    lecture de l'entrepôt via `dbt show` (aucun driver)
  files.py        écriture .sql et upsert schema.yml (ruamel, préserve le style)
  recipes/        les scripts visuels — voir ci-dessous
  server/         API FastAPI + WebSocket — voir ci-dessous
  static/         interface + logo.png + polices embarquées
tests/           suite pytest
pyproject.toml   paquet ; dbt-core pour seule dépendance métier
```

dbt est **exécuté**, pas imité : `dbtRunner().invoke([...])` en mémoire. Le
manifest, les résultats et les logs viennent de dbt lui-même.

### `recipes/` — chaque module ne connaît que ceux qui le précèdent

```
errors       le refus d'une recipe
vocabulary   les valeurs fermées que dbt impose, libellés compris
dialect      l'entrepôt pour lequel on écrit, et ses trois noms
sql          citer un identifiant, écrire un littéral
writers      ce qui ne s'écrit pas pareil d'une famille à l'autre
names        ce qu'on accepte d'écrire : noms et expressions
columns      l'état des colonnes au fil d'un script, le SQL d'une étape
spec         la forme d'un script visuel
refs         les ref() et source() : les écrire, les faire suivre
processors   une fonction par geste de la recipe Préparer
describe     la phrase lue sur la carte d'une étape
config       le {{ config() }} et la machinerie incrémentale
compilers    recipe → SQL dbt, un compilateur par type
storage      les scripts visuels sur le disque
```

`__init__.py` n'est qu'une façade : `from pliq import recipes as rcp` puis
`rcp.compile_recipe(...)` s'écrit sans savoir où vit quoi.

### `server/` — l'atelier ouvert, et les routes autour

```
atelier      le projet ouvert, le verrou, les garde-fous, la bascule
http         sous quel nom et depuis quelle page on accepte d'être joint
bodies       les corps de requête
noeuds       retrouver un nœud dbt, dire ce qu'on a le droit d'en faire
versions     refuser d'écraser une version qu'on n'a pas lue
lectures     ce que les routes lisent avant de répondre
routes_*     les routes, une fabrique par domaine
```

Le projet ouvert vit sur un objet `Atelier` plutôt que dans des cellules de
fermeture : tout le reste le prend en premier argument, et devient donc
appelable depuis un test sans passer par un client HTTP.

### `static/` — aucun build, aucune dépendance

L'interface est un paquet de **modules ES** sous `static/js/`, que le
navigateur résout lui-même : pas de bundler, pas de `node_modules`, rien à
installer pour y toucher. `app.js` n'est qu'un point d'entrée.

Une règle le rend possible : **aucun module n'agit pendant son chargement**.
Les branchements d'événements vivent dans un `brancher()` par module, appelés
depuis `app.js` une fois tout évalué, et `boot()` part en dernier. Ces modules
se référencent en rond — le Flow ouvre une recipe qui recharge le Flow — et
c'est ce qui rend les cycles inoffensifs. Un test refuse toute instruction
exécutable au premier niveau d'un module.

Les listes déroulantes ne sont écrites nulle part dans le JS : opérateurs,
types, fonctions de fenêtre, agrégations, matérialisations et stratégies
viennent toutes de `/api/processors`, donc de `recipes/vocabulary.py`. Le
libellé du menu et celui de la carte d'étape sont le même texte.

Les polices sont embarquées (`static/fonts/`) : l'atelier tourne sur un poste,
souvent hors ligne, et n'a rien à dire à un CDN sur ce qu'on y fait.

`static/doc/` suit la même règle : la documentation est un paquet de pages
Markdown, rendues par un lecteur maison de deux cents lignes
(`static/doc/markdown.js`) qui ne comprend que la syntaxe employée par les
pages — un test refuse tout ce qu'il ne saurait pas rendre. Aucune
bibliothèque, aucun CDN, et le `.md` reste lisible dans un éditeur.

### La palette

La palette tient en dix couleurs, déclarées une fois pour toutes dans
`static/palette.css` et préfixées `--brand-` — la feuille de l'atelier et celle
de la documentation la chargent toutes deux, aucune ne la recopie ; viennent ensuite des nuances
dérivées (éclaircies ou assombries de ces dix teintes, pour les filets, les
fonds et les petits textes), puis les rôles de l'atelier — `--ds`, `--rc`,
`--ink`, `--line`… Une couleur ne s'écrit jamais en dur dans une règle : elle
passe par un rôle, qui passe par une nuance, qui vient de la palette.

| Rôle | Couleur |
| --- | --- |
| Bleu nuit — bandeau, titres, sélection, action de confirmation | `#002340` |
| Orange — soulignements, CTA, puces, lignée mise en avant | `#FBA31C` |
| Gris bleuté — texte courant | `#6E7988` |
| Gris foncé — texte secondaire | `#454D58` |
| Bleu pâle — fonds de blocs, sélection dans les listes | `#DEECF6` |
| Vert pâle — colonnes touchées par l'étape prévisualisée | `#CCDDAA` |
| Bleu moyen — liens, illustrations, datasets du Flow | `#5BA0D1` |
| Rouge — alertes | `#C3271A` |

Le logo est un aplat noir d'un seul tenant : il sert de pochoir. Sur le
bandeau bleu nuit un filtre le retourne en blanc, sur l'accueil un masque CSS
le teinte en bleu nuit — un seul fichier pour les deux usages.

## Tests

```bash
uv pip install -e '.[dev]'   # une fois

./test.sh                 # tout, dbt compris (~4 min)
./test.sh -m "not dbt"    # la boucle rapide, aucun dbt lancé (~4 s)
```

Trois niveaux, décrits dans [`tests/README.md`](https://github.com/AdrienBouhot/pliq/blob/main/tests/README.md) :

- **unitaire** — modules purs, aucun dbt ;
- **sémantique** — le SQL compilé par une recipe est *exécuté* sur un DuckDB en
  mémoire, et on regarde les lignes qui sortent : c'est ce qui prouve qu'une
  recipe fait ce qu'elle annonce ;
- **bout en bout** (`-m dbt`) — un vrai projet dbt, un vrai `dbt build`, l'API
  HTTP, jusqu'à la table écrite dans l'entrepôt.

`tests/test_frontend.py` vérifie en plus que l'interface et le serveur restent
d'accord : routes appelées, processeurs éditables, options des menus.

La suite tourne aussi sur la plus vieille version de dbt que `pyproject.toml`
annonce, et pas seulement sur la plus récente : ce qui ne marche qu'à partir de
dbt 1.10 ne doit pas être écrit dans un projet en 1.9.

Un bug qu'on ne corrige pas tout de suite se décrit par un test marqué
`xfail(strict=True)` : il dit le comportement attendu, passera au vert le jour
de la correction, et la suite échouera si quelqu'un corrige sans retirer le
marqueur. Il n'y en a aucun en ce moment.

## Ce que l'atelier couvre de dbt, et ce qu'il ne couvre pas

La table de couverture — matérialisations, incrémental, seeds, sources, tests,
tags, snapshots, contrats… — et les choix qui l'expliquent (aperçu sans rien
écrire, aucune connexion à l'entrepôt, échantillon contre contrôle complet)
sont dans la documentation :
[« Ce que l'atelier couvre de dbt »](https://github.com/AdrienBouhot/pliq/blob/main/pliq/static/doc/pages/couverture-dbt.md).

Les limites assumées — pas de rétro-ingénierie, l'analyse d'impact qui avertit
sans réparer, les écarts de dialecte, pas encore de Git dans l'interface — sont
là : [« Limites actuelles »](https://github.com/AdrienBouhot/pliq/blob/main/pliq/static/doc/pages/limites.md).

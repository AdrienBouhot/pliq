# Documenter

**Documenter & tester**, dans le panneau du [Flow](flow.md), ouvre la même fiche
pour **n'importe quel dataset** — modèle, seed, ou table de source :

- la **description de la table**, qui ressort dans `dbt docs` et le catalogue ;
- ses **tags**, qui sont des sélecteurs (`dbt build --select tag:finance`) ;
- la **description de chaque colonne**, et ses [tests](tests.md).

## L'atelier écrit là où dbt lit déjà

Il suit le `patch_path` du manifest : un modèle documenté dans un fichier
particulier reste documenté au même endroit. Un seed va dans
`seeds/schema.yml`, une table de source dans le `sources.yml` qui la déclare,
sous `tables:`.

> [!note] Le reste du fichier — commentaires, ordre, guillemets, autres entrées — n'est pas touché. C'est ce qui rend la fiche utilisable sur un projet existant dont les YAML sont écrits à la main, et relus en revue de code.

Les tags suivent la convention de chaque famille : sous `config:` pour un modèle
ou un seed, au premier niveau pour une source.

## Ce qui n'est pas exposé

Les **doc blocks** (`{% docs %}`), `meta` et `persist_docs` ne sont pas gérés
par l'atelier. Un projet qui les emploie n'en perd rien : ils sont dans des
fichiers que l'atelier ne réécrit pas.

## Une source se documente aussi

C'est même là que ça compte le plus : une source est la frontière du projet, et
sa fiche porte en plus sa colonne de chargement et ses seuils de fraîcheur. Voir
[La fraîcheur des sources](fraicheur.md).

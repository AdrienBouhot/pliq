# Tests

```bash
./test.sh                 # tout, dbt compris (~4 min)
./test.sh -m "not dbt"    # la boucle rapide, aucun dbt lancé (~4 s)
./test.sh tests/test_recipes_execution.py -k join
```

Les deux durées sont mesurées, pas estimées : 1 112 tests en 240 s, dont 896
sans dbt en 3,9 s (macOS, Python 3.11). Ce sont les 217 tests marqués `dbt` qui
coûtent : chacun construit un vrai projet et lance un vrai `dbt build`.

Outillage : `uv pip install -e '.[dev]'` — l'extra `dev` du `pyproject.toml`
(pytest, httpx). Il n'y a pas de `requirements-dev.txt`.

Quelques tests font tourner l'interface sous **node** ; sans lui, ils se
sautent — un saut n'est pas un échec, et c'est justement ce qu'il faut savoir.

## Trois niveaux

| Fichier | Ce qui est vérifié | dbt ? |
|---|---|---|
| `test_recipes_execution.py` | le SQL compilé est **exécuté sur DuckDB** : une recipe fait-elle ce qu'elle annonce ? | non |
| `test_recipes_guardrails.py` | ce que l'atelier doit refuser, et le message qu'il donne | non |
| `test_recipes_sql.py` | le fichier `.sql` écrit : config, CTE commentés, deltas de colonnes | non |
| `test_recipes_storage.py` | le script visuel dans `.pliq/recipes/` | non |
| `test_files.py` | écriture des YAML dbt sans abîmer l'existant | non |
| `test_datasets.py` | sources, couche suggérée | non |
| `test_config.py`, `test_projects.py` | découverte du projet, registre, création, suppression | non |
| `test_graph.py` | manifest → Flow biparti | non |
| `test_warehouse.py` | lecture via `dbt show` : conversion agate, inventaire, messages | oui (2) |
| `test_profiling.py` | significations devinées, barres de qualité, suggestions | non |
| `test_frontend.py` | l'interface et le serveur restent d'accord (routes, processeurs, options) | non |
| `test_api.py` | **bout en bout** : vrai projet, vrai `dbt build`, API HTTP | oui |

Le niveau qui compte le plus est le premier : on ne compare pas des chaînes de
SQL, on crée des tables dans un DuckDB en mémoire, on exécute ce que la recipe
a produit, et on regarde les lignes qui sortent.

## Écrire un test de recipe

```python
def test_arrondir(orders: Bench):
    cols, rows = orders.run(prepare_spec([
        {"type": "round", "params": {"column": "amount_eur", "decimals": 2}},
    ]))
    assert rows[0][cols.index("amount_eur")] == pytest.approx(100.46)
```

`orders` est un banc d'essai (`conftest.Bench`) avec une table volontairement
sale — trous, casse mélangée, doublons. `bench.table(...)` en ajoute d'autres,
`bench.source_table(...)` en déclare comme sources.

## Tester un deuxième build incrémental

Un modèle incrémental ne montre ses défauts qu'au **deuxième** build : le
premier écrit la table en entier, `is_incremental()` étant faux. `bench.build()`
rejoue ce cycle — il crée la table au premier appel, puis active le bloc
incrémental, pointe `{{ this }}` sur la table déjà écrite et applique la
stratégie déclarée, comme le fait la matérialisation de dbt.

```python
_, rows = bench.build(spec)          # premier build : tout
bench.con.execute("insert into main.lignes values ('a', 5, '2024-01-02')")
_, rows = bench.build(spec)          # deuxième : c'est lui qui révèle
```

## Ce qui est isolé

- le registre `~/.pliq/projects.json` est détourné vers un dossier temporaire ;
- le répertoire courant est restauré après chaque test (l'atelier fait
  `os.chdir` pour de vrai) ;
- aucun test n'écrit dans `examples/` ni dans le dépôt.

## Bugs connus

Aucun pour le moment. Les trois qui étaient tenus par des `xfail(strict=True)`
— le filtre « retirer » qui emportait les lignes vides, « Capitalisé » qui
compilait `initcap()`, « Changer le type » qui refusait `decimal(18,2)` — sont
corrigés, et leurs tests sont devenus des tests ordinaires.

Un bug qu'on ne corrige pas tout de suite se décrit ici par un test marqué
`xfail(strict=True)` : il dit le comportement **attendu**, il passera au vert le
jour de la correction, et la suite échouera si quelqu'un corrige sans retirer le
marqueur.

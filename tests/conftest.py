"""Fixtures partagées par la suite de tests d'Pliq.

Trois niveaux, du plus rapide au plus lent :

1. **unitaire** — les modules purs (recipes, files, profiling, graph…). Aucun
   dbt, aucun réseau, aucun fichier hors de `tmp_path`.
2. **sémantique** — le SQL compilé par une recipe est *vraiment exécuté* sur un
   DuckDB en mémoire (fixture `bench`). C'est ce qui prouve qu'une recipe fait
   ce qu'elle annonce, et pas seulement qu'elle produit du texte.
3. **bout en bout** — marqués `@pytest.mark.dbt` : un vrai projet dbt, un vrai
   `dbt build`, l'API HTTP par-dessus.

Deux précautions valables partout : le registre des projets (`~/.pliq`) est
détourné vers un dossier temporaire, et le répertoire courant est restauré après
chaque test — l'atelier fait `os.chdir` pour de vrai.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from pliq import projects, recipes as rcp
from pliq.config import Settings, load_settings

# ------------------------------------------------- résolution de ref() (test)
#
# En production, l'atelier n'a pas besoin de ça : il envoie le Jinja tel quel à
# `dbt show`, qui le compile. Mais le banc d'essai ci-dessous exécute le SQL
# compilé sur un DuckDB en mémoire, sans projet dbt — il lui faut donc de quoi
# transformer `{{ ref('x') }}` en `main.x`. C'est de l'échafaudage de test, et
# ça n'a volontairement pas à être complet : tout Jinja qui resterait fait
# échouer le test au lieu de passer silencieusement.

_REF_RE = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_REF2_RE = re.compile(
    r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}"
)
_SOURCE_RE = re.compile(
    r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}"
)
_CONFIG_RE = re.compile(r"\{\{\s*config\([\s\S]*?\)\s*\}\}")
_INCREMENTAL_RE = re.compile(
    r"\{%-?\s*if\s+is_incremental\(\)\s*-?%\}[\s\S]*?\{%-?\s*endif\s*-?%\}"
)
# Le deuxième build, lui, garde le corps du bloc et jette seulement les balises.
_IF_INCREMENTAL_RE = re.compile(r"\{%-?\s*if\s+is_incremental\(\)\s*-?%\}")
_ENDIF_RE = re.compile(r"\{%-?\s*endif\s*-?%\}")
_THIS_RE = re.compile(r"\{\{\s*this\s*\}\}")


def resolve_refs_for_test(sql: str, manifest, *, this: str | None = None) -> str:
    by_name = {
        n.name: n.relation_name
        for n in manifest.nodes.values()
        if getattr(n, "relation_name", None)
    }
    by_source = {
        (s.source_name, s.name): s.relation_name
        for s in manifest.sources.values()
        if getattr(s, "relation_name", None)
    }

    def sub_ref(m):
        name = m.group(len(m.groups()))
        rel = by_name.get(name)
        assert rel, f"banc d'essai : ref('{name}') inconnue"
        return rel

    def sub_source(m):
        rel = by_source.get((m.group(1), m.group(2)))
        assert rel, f"banc d'essai : source('{m.group(1)}','{m.group(2)}') inconnue"
        return rel

    out = _CONFIG_RE.sub("", sql).lstrip()
    if this is None:
        # Premier build : la table n'existe pas, `is_incremental()` est faux.
        out = _INCREMENTAL_RE.sub("", out)
    else:
        out = _IF_INCREMENTAL_RE.sub("", out)
        out = _ENDIF_RE.sub("", out)
        out = _THIS_RE.sub(this, out)
    out = _REF2_RE.sub(sub_ref, out)
    out = _REF_RE.sub(sub_ref, out)
    out = _SOURCE_RE.sub(sub_source, out)
    assert "{{" not in out and "{%" not in out, f"Jinja non résolu par le banc : {out}"
    return out


# ------------------------------------------------------------------ isolation


@pytest.fixture(autouse=True, scope="session")
def _isolated_registry(tmp_path_factory):
    """Le registre vit dans ~/.pliq/projects.json : jamais pendant les tests."""
    mp = pytest.MonkeyPatch()
    mp.setattr(
        projects, "REGISTRY", tmp_path_factory.mktemp("registry") / "projects.json"
    )
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _duckdb_dialect():
    """L'entrepôt de la suite est DuckDB : le compilateur doit le savoir.

    En production le dialecte vient du projet ouvert. Ici le SQL compilé est
    exécuté sur DuckDB — le laisser sur « entrepôt inconnu » ferait compiler un
    SQL générique que rien n'exécute, et masquerait les écarts de dialecte.

    Le bloc rend l'entrepôt précédent plutôt que de forcer « inconnu » : un
    test qui compile pour Snowflake ne laisse ainsi rien derrière lui, même
    s'il échoue au milieu.
    """
    with rcp.using_dialect("duckdb"):
        yield


@pytest.fixture(autouse=True)
def _keep_cwd():
    """`_switch` et `__main__` font os.chdir : on rend le répertoire courant."""
    before = Path.cwd()
    yield
    os.chdir(before)


# --------------------------------------------------------------- projet à vide


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


DBT_PROJECT_YML = """\
name: 'demo'
version: '1.0.0'
config-version: 2
profile: 'demo'

model-paths: ["models"]
seed-paths: ["seeds"]

models:
  demo:
    staging:
      +materialized: view
    marts:
      +materialized: table
"""

PROFILES_YML = """\
demo:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: dev.duckdb
      threads: 1
    prod:
      type: duckdb
      path: prod.duckdb
      threads: 1
"""


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Un projet dbt minimal sur le disque, sans passer par dbt."""
    root = tmp_path / "demo"
    write(root / "dbt_project.yml", DBT_PROJECT_YML)
    write(root / "profiles.yml", PROFILES_YML)
    (root / "models" / "staging").mkdir(parents=True)
    (root / "models" / "marts").mkdir(parents=True)
    (root / "seeds").mkdir()
    return root


@pytest.fixture
def settings(project_dir: Path) -> Settings:
    return load_settings(project_dir=str(project_dir))


@pytest.fixture(scope="session")
def built_project(tmp_path_factory) -> Settings:
    """Un vrai projet dbt, vraiment construit : pour tester le chemin `dbt show`.

    Construit une fois pour la session — un `dbt build` coûte quelques secondes.
    """
    root = tmp_path_factory.mktemp("entrepot") / "demo"
    write(root / "dbt_project.yml", DBT_PROJECT_YML)
    # Chemin absolu : dbt-duckdb résout un `path:` relatif depuis le cwd du
    # process, et la fixture ne doit pas dépendre de qui a fait chdir avant elle.
    write(
        root / "profiles.yml",
        f"""\
demo:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: {root / 'dev.duckdb'}
      threads: 1
""",
    )
    write(
        root / "models" / "stg_orders.sql",
        "{{ config(materialized='table') }}\n"
        "select * from (values (1, 'completed'), (2, null)) t(order_id, status)",
    )
    write(root / "models" / "v_orders.sql", "select * from {{ ref('stg_orders') }}")

    settings = load_settings(project_dir=str(root))
    from pliq.dbt_service import DbtService

    # `reset=True` : un run rend la base en sortant, et l'atelier ouvrira la
    # sienne juste après — DuckDB n'accepte qu'un écrivain.
    res = DbtService(settings).invoke(["build"], reset=True)
    assert res.success, "le projet de test doit se construire"
    return settings


def release_duckdb_for_test() -> None:
    """Ferme l'instance que dbt-duckdb garde dans un singleton de classe.

    Pliq n'a plus besoin de ça : il ne se connecte jamais à l'entrepôt en
    direct. Mais un test qui veut inspecter ou préparer la base lui-même doit
    d'abord libérer la connexion de dbt — DuckDB refuse deux configurations
    différentes pour un même fichier dans un même process.
    """
    try:
        from dbt.adapters.duckdb.connections import DuckDBConnectionManager as manager
    except ImportError:
        return
    env = getattr(manager, "_ENV", None)
    if env is None:
        return
    try:
        env.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        manager._ENV = None
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------- faux manifest


def fake_node(
    uid: str,
    name: str,
    *,
    resource_type: str = "model",
    path: str = "",
    depends_on: tuple[str, ...] = (),
    materialized: str = "view",
    relation: str | None = None,
    raw_code: str = "",
    description: str = "",
    tags: tuple[str, ...] = (),
    columns: tuple[str, ...] = (),
    schema: str = "main",
    database: str = "dev",
    source_name: str = "",
    column_name: str | None = None,
    test_name: str | None = None,
    severity: str = "error",
    identifier: str | None = None,
) -> SimpleNamespace:
    """Un nœud de manifest dbt réduit à ce que lit l'atelier."""
    return SimpleNamespace(
        unique_id=uid,
        name=name,
        resource_type=resource_type,
        original_file_path=path or f"models/{name}.sql",
        depends_on=SimpleNamespace(nodes=list(depends_on)),
        config=SimpleNamespace(materialized=materialized, severity=severity),
        relation_name=relation,
        raw_code=raw_code,
        description=description,
        tags=list(tags),
        columns={c: SimpleNamespace(description="", data_type=None) for c in columns},
        schema=schema,
        database=database,
        source_name=source_name,
        identifier=identifier or name,
        alias=name,
        column_name=column_name,
        test_metadata=SimpleNamespace(name=test_name) if test_name else None,
        package_name="demo",
        patch_path=None,
    )


class FakeManifest:
    def __init__(
        self,
        nodes: list[SimpleNamespace] | None = None,
        sources: list[SimpleNamespace] | None = None,
    ):
        self.nodes = {n.unique_id: n for n in (nodes or [])}
        self.sources = {s.unique_id: s for s in (sources or [])}


# ------------------------------------------------- banc d'essai SQL (DuckDB)


class Bench:
    """Compile une recipe, résout ses ref()/source(), et l'exécute sur DuckDB.

    C'est le seul moyen honnête de tester un compilateur SQL : on ne compare pas
    des chaînes, on regarde les lignes qui sortent.
    """

    def __init__(self) -> None:
        self.con = duckdb.connect(":memory:")
        self._nodes: list[SimpleNamespace] = []
        self._sources: list[SimpleNamespace] = []

    # --- entrées

    def table(self, name: str, ddl: str, rows: list[tuple] | None = None) -> str:
        """Crée `main.<name>` et la déclare comme modèle dbt référençable."""
        self.con.execute(f"create table main.{name} ({ddl})")
        if rows:
            placeholders = ", ".join("?" for _ in rows[0])
            self.con.executemany(
                f"insert into main.{name} values ({placeholders})", rows
            )
        self._nodes.append(
            fake_node(f"model.demo.{name}", name, relation=f"main.{name}")
        )
        return name

    def source_table(
        self, source: str, name: str, ddl: str, rows: list[tuple] | None = None
    ) -> str:
        self.con.execute(f"create table main.{name} ({ddl})")
        if rows:
            placeholders = ", ".join("?" for _ in rows[0])
            self.con.executemany(
                f"insert into main.{name} values ({placeholders})", rows
            )
        self._sources.append(
            fake_node(
                f"source.demo.{source}.{name}",
                name,
                resource_type="source",
                source_name=source,
                relation=f"main.{name}",
            )
        )
        return name

    # --- exécution

    @property
    def manifest(self) -> FakeManifest:
        return FakeManifest(self._nodes, self._sources)

    def columns(self, *tables: str) -> dict[str, list[dict]]:
        """Les colonnes de tables de l'entrepôt, rangées sous leur propre nom."""
        return {t: self.columns_of(t) for t in tables}

    def columns_of(self, table: str) -> list[dict]:
        cur = self.con.execute(f"select * from main.{table} limit 0")
        return [{"name": d[0], "type": str(d[1])} for d in cur.description]

    def input_columns(self, spec: dict) -> dict[str, list[dict]]:
        """Les colonnes de chaque entrée, rangées par alias — comme le serveur.

        L'alias n'est pas le nom de la table : une autojointure lit deux fois la
        même table sous deux noms, et c'est l'alias qui les distingue.
        """
        input_entries = spec.get("inputs") or []
        rcp.check_max_inputs(spec)
        aliases = rcp.input_aliases(input_entries)
        return {
            alias: self.columns_of(inp.get("ref") or inp.get("table") or alias)
            for alias, inp in zip(aliases, input_entries, strict=True)
        }

    def compile(self, spec: dict) -> str:
        return rcp.compile_recipe(spec, self.input_columns(spec))

    def run(self, spec: dict, upto: int | None = None) -> tuple[list[str], list[tuple]]:
        """(noms de colonnes, lignes) de la recipe exécutée pour de vrai."""
        cols = self.input_columns(spec)
        if upto is not None:
            sql, _, _ = rcp.compile_prepare(spec, cols, upto=upto)
        else:
            sql = rcp.compile_recipe(spec, cols)
        return self.sql(resolve_refs_for_test(sql, self.manifest))

    def build(self, spec: dict, target: str = "cible") -> tuple[list[str], list[tuple]]:
        """Un `dbt build` simulé, rejouable : c'est le *deuxième* qui révèle.

        Premier appel, la table n'existe pas : `is_incremental()` est faux et le
        modèle s'écrit en entier. Aux suivants, on active le bloc incrémental,
        on pointe `{{ this }}` sur la table déjà écrite, puis on applique la
        stratégie déclarée — comme le fait la matérialisation de dbt.
        """
        sql = rcp.compile_recipe(spec, self.input_columns(spec))
        exists = self.con.execute(
            "select count(*) from duckdb_tables() "
            f"where schema_name = 'main' and table_name = '{target}'"
        ).fetchone()[0]

        if not exists:
            body = resolve_refs_for_test(sql, self.manifest)
            self.con.execute(f"create table main.{target} as {body}")
            return self.sql(f"select * from main.{target}")

        body = resolve_refs_for_test(sql, self.manifest, this=f"main.{target}")
        out = spec.get("output") or {}
        strategy = out.get("incremental_strategy") or "delete+insert"
        self.con.execute(f"create or replace temp table _arrivage as {body}")
        if strategy != "append":
            keys = [k for k in (out.get("unique_key") or []) if k]
            assert keys, "delete+insert sans clé : dbt refuserait aussi"
            key = ", ".join(f'"{k}"' for k in keys)
            if len(keys) > 1:
                key = f"({key})"
            self.con.execute(
                f"delete from main.{target} "
                f"where {key} in (select {key} from _arrivage)"
            )
        self.con.execute(f"insert into main.{target} select * from _arrivage")
        return self.sql(f"select * from main.{target}")

    def sql(self, statement: str) -> tuple[list[str], list[tuple]]:
        cur = self.con.execute(statement)
        return [d[0] for d in cur.description], cur.fetchall()

    def close(self) -> None:
        self.con.close()


@pytest.fixture
def bench() -> Any:
    b = Bench()
    yield b
    b.close()


@pytest.fixture
def orders(bench: Bench) -> Bench:
    """Une table de commandes volontairement sale : trous, casse, doublons."""
    bench.table(
        "stg_orders",
        "order_id integer, customer_id integer, status varchar, "
        "amount_eur double, ordered_at varchar, country varchar",
        [
            (1, 10, "completed", 100.456, "01/02/2024", "fr"),
            (2, 10, "PENDING", 20.0, "03/02/2024", "FR"),
            (3, 11, "completed", 5.5, "04/02/2024", None),
            (4, 12, None, None, "05/02/2024", " es "),
            (4, 12, None, None, "05/02/2024", " es "),
        ],
    )
    return bench


def prepare_spec(
    steps: list[dict],
    *,
    name: str = "orders_prepared",
    ref: str = "stg_orders",
    output: dict | None = None,
) -> dict:
    """Raccourci : une recipe Préparer sur une entrée, avec ses étapes."""
    return {
        "name": name,
        "type": "prepare",
        "inputs": [{"ref": ref, "alias": ref}],
        "output": output or {"materialized": "view"},
        "steps": [{"id": f"s{i}", "enabled": True, **s} for i, s in enumerate(steps)],
    }

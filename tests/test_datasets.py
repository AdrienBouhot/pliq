"""Faire entrer une table dans le Flow : sources, couche suggérée."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pliq import datasets
from pliq.config import Settings
from pliq.datasets import DatasetError

from .conftest import FakeManifest, fake_node, write

# ----------------------------------------------------------------- sources


SOURCES = """\
version: 2

sources:
  - name: raw
    schema: main
    tables:
      - name: raw_orders
"""


def test_declarer_une_table_dans_un_groupe_existant(settings: Settings):
    write(settings.project_dir / "models/staging/sources.yml", SOURCES)
    out = datasets.declare_source(
        settings,
        source_name="raw",
        schema="main",
        tables=[
            {"name": "raw_orders"},
            {"name": "raw_customers", "description": "Clients"},
        ],
    )
    assert out["added"] == ["raw_customers"]
    assert out["skipped"] == ["raw_orders"], "on complète, on ne remplace jamais"
    assert out["path"] == "models/staging/sources.yml"
    tables = yaml.safe_load((settings.project_dir / out["path"]).read_text())[
        "sources"
    ][0]["tables"]
    assert [t["name"] for t in tables] == ["raw_orders", "raw_customers"]


SOURCES_TABLES_SCALAIRE = """\
version: 2

sources:
  - name: raw
    schema: main
    tables: raw_orders
"""


def test_un_tables_qui_n_est_pas_une_liste_est_refuse_et_non_une_panne(
    settings: Settings,
):
    """Un `sources.yml` s'édite aussi à la main, et pas toujours bien.

    La normalisation ne visait que `tables:` absent (`is None`). Un scalaire
    ou une table YAML passait la garde, puis `group["tables"].append(...)`
    levait une `AttributeError` qu'aucune route n'attrape : 500, sans dire
    quel fichier réparer.

    Le refus nomme le fichier, et n'écrase pas ce qui s'y trouve : c'est du
    travail écrit à la main.
    """
    write(settings.project_dir / "models/staging/sources.yml", SOURCES_TABLES_SCALAIRE)
    with pytest.raises(DatasetError, match="n'est pas une liste"):
        datasets.declare_source(
            settings,
            source_name="raw",
            schema="main",
            tables=[{"name": "raw_customers"}],
        )
    # Le fichier est intact : rien n'a été réécrit sous l'utilisateur.
    text = (settings.project_dir / "models/staging/sources.yml").read_text()
    assert "tables: raw_orders" in text


def test_declarer_un_nouveau_groupe_cree_le_fichier_a_la_convention(settings: Settings):
    out = datasets.declare_source(
        settings,
        source_name="raw",
        schema="main",
        database="dev",
        description="Brut",
        tables=[{"name": "raw_orders"}],
    )
    assert out["path"] == "models/staging/sources.yml"
    group = yaml.safe_load((settings.project_dir / out["path"]).read_text())["sources"][
        0
    ]
    assert group["name"] == "raw" and group["schema"] == "main"
    assert group["database"] == "dev" and group["description"] == "Brut"


def test_ajouter_une_table_d_un_autre_schema_a_un_groupe_est_refuse(
    settings: Settings,
):
    """Un groupe de sources porte *un* schéma, et ses tables en héritent.

    Le schéma demandé n'était pas comparé à celui du groupe : ajouter
    `schema_b.customers` au groupe `raw`, déjà rattaché à `main`, déclarait en
    fait `main.customers`. Au mieux dbt ne trouve pas la table, au pire il en
    lit une autre qui porte le même nom.
    """
    path = settings.project_dir / "models/staging/sources.yml"
    write(path, SOURCES)
    before = path.read_text()

    with pytest.raises(DatasetError) as exc:
        datasets.declare_source(
            settings,
            source_name="raw",
            schema="schema_b",
            tables=[{"name": "customers"}],
        )
    assert "main" in str(exc.value) and "schema_b" in str(exc.value)
    assert path.read_text() == before, "rien ne doit être écrit"


def test_un_groupe_sans_schema_explicite_lit_celui_de_son_nom(settings: Settings):
    """dbt prend le nom du groupe quand `schema:` manque : c'est à cela qu'on
    compare, sinon on lui en collait un autre au passage."""
    path = settings.project_dir / "models/staging/sources.yml"
    write(path, "version: 2\n\nsources:\n  - name: raw\n    tables:\n      - name: t\n")

    with pytest.raises(DatasetError, match="raw"):
        datasets.declare_source(
            settings, source_name="raw", schema="main", tables=[{"name": "u"}]
        )

    out = datasets.declare_source(
        settings, source_name="raw", schema="raw", tables=[{"name": "u"}]
    )
    assert out["added"] == ["u"]


def test_ajouter_une_table_d_une_autre_base_a_un_groupe_est_refuse(settings: Settings):
    path = settings.project_dir / "models/staging/sources.yml"
    write(
        path,
        "version: 2\n\nsources:\n  - name: raw\n    schema: main\n"
        "    database: prod\n    tables:\n      - name: t\n",
    )
    with pytest.raises(DatasetError, match="base"):
        datasets.declare_source(
            settings,
            source_name="raw",
            schema="main",
            database="dev",
            tables=[{"name": "u"}],
        )

    # Sans base demandée, dbt prend celle de la cible : rien à refuser.
    out = datasets.declare_source(
        settings, source_name="raw", schema="main", tables=[{"name": "u"}]
    )
    assert out["added"] == ["u"]


def test_ajouter_une_table_d_une_autre_base_a_un_groupe_qui_herite_est_refuse(
    settings: Settings,
):
    """Un groupe sans `database:` n'est pas un groupe sans base.

    dbt lui donne celle de la cible, et ses tables en héritent comme elles
    héritent du schéma. Comparer les deux `database:` *écrits* laissait donc
    passer le cas courant : ajouter `another_database.main.orders` à un groupe
    qui lit `dev` écrivait un YAML qui envoie dbt lire `dev.main.orders` — sans
    un mot, si cette table-là existe aussi.
    """
    path = settings.project_dir / "models/staging/sources.yml"
    write(path, SOURCES)
    before = path.read_text()
    manifest = FakeManifest(
        sources=[
            fake_node(
                "source.demo.raw.raw_orders",
                "raw_orders",
                resource_type="source",
                source_name="raw",
                schema="main",
                database="dev",
            )
        ]
    )

    with pytest.raises(DatasetError) as exc:
        datasets.declare_source(
            settings,
            source_name="raw",
            schema="main",
            database="another_database",
            tables=[{"name": "orders"}],
            manifest=manifest,
        )
    assert "dev" in str(exc.value) and "another_database" in str(exc.value)
    assert path.read_text() == before, "rien ne doit être écrit"

    # La base héritée, elle, passe — et n'est pas recopiée dans le YAML.
    out = datasets.declare_source(
        settings,
        source_name="raw",
        schema="main",
        database="dev",
        tables=[{"name": "orders"}],
        manifest=manifest,
    )
    assert out["added"] == ["orders"]
    group = yaml.safe_load(path.read_text())["sources"][0]
    assert "database" not in group


def test_la_base_d_un_groupe_se_compare_sans_egard_a_la_casse(settings: Settings):
    """`information_schema` rend `DEV` là où le profil écrit `dev` : refuser
    là-dessus bloquerait une déclaration parfaitement juste."""
    write(settings.project_dir / "models/staging/sources.yml", SOURCES)
    manifest = FakeManifest(
        sources=[
            fake_node(
                "source.demo.raw.raw_orders",
                "raw_orders",
                resource_type="source",
                source_name="raw",
                schema="main",
                database="dev",
            )
        ]
    )
    out = datasets.declare_source(
        settings,
        source_name="raw",
        schema="main",
        database="DEV",
        tables=[{"name": "orders"}],
        manifest=manifest,
    )
    assert out["added"] == ["orders"]


def test_le_schema_d_un_groupe_se_compare_sans_egard_a_la_casse(settings: Settings):
    """Snowflake replie ses identifiants en majuscules : l'inventaire rend
    `MAIN` là où le YAML du groupe porte `main`. La base ignorait déjà la
    casse ; le schéma la respectait, et refusait la même déclaration."""
    write(settings.project_dir / "models/staging/sources.yml", SOURCES)
    out = datasets.declare_source(
        settings,
        source_name="raw",
        schema="MAIN",
        tables=[{"name": "orders"}],
    )
    assert out["added"] == ["orders"]


def test_un_groupe_que_dbt_ne_connait_pas_encore_s_en_tient_au_yaml(
    settings: Settings,
):
    """Sans table déclarée, dbt ne sait rien du groupe : on ne peut comparer
    qu'à ce qui est écrit, et l'absence de manifeste ne bloque rien."""
    write(
        settings.project_dir / "models/staging/sources.yml",
        "version: 2\n\nsources:\n  - name: raw\n    schema: main\n    tables: []\n",
    )
    out = datasets.declare_source(
        settings,
        source_name="raw",
        schema="main",
        database="another_database",
        tables=[{"name": "orders"}],
        manifest=FakeManifest(),
    )
    assert out["added"] == ["orders"]


def test_declarer_une_source_sans_schema_ni_table(settings: Settings):
    with pytest.raises(DatasetError, match="schéma"):
        datasets.declare_source(
            settings, source_name="raw", schema="", tables=[{"name": "t"}]
        )
    with pytest.raises(DatasetError, match="au moins une table"):
        datasets.declare_source(settings, source_name="raw", schema="main", tables=[])


# ------------------------------------------------ ce que dbt connaît déjà


def _manifest() -> FakeManifest:
    return FakeManifest(
        nodes=[
            fake_node("model.demo.stg_orders", "stg_orders", schema="staging"),
            fake_node(
                "seed.demo.raw_customers",
                "raw_customers",
                resource_type="seed",
                schema="main",
            ),
            fake_node("test.demo.x", "x", resource_type="test"),
        ],
        sources=[
            fake_node(
                "source.demo.raw.raw_orders",
                "raw_orders",
                resource_type="source",
                source_name="raw",
                schema="main",
            )
        ],
    )


def test_index_des_relations_deja_declarees():
    """L'identité d'une relation tient en trois niveaux, base comprise."""
    known = datasets.known_relations(_manifest())
    assert known[("dev", "staging", "stg_orders")]["kind"] == "model"
    assert known[("dev", "main", "raw_customers")]["kind"] == "seed"
    assert known[("dev", "main", "raw_orders")] == {
        "kind": "source",
        "label": "raw.raw_orders",
        "id": "source.demo.raw.raw_orders",
    }
    assert not any(v["kind"] == "test" for v in known.values())


def test_groupes_de_sources_existants(settings: Settings):
    group_list = datasets.existing_sources(settings, _manifest())
    assert group_list[0]["name"] == "raw" and group_list[0]["tables"] == ["raw_orders"]


# ------------------------------------------------------------ couche suggérée


def test_layer_of():
    paths = [Path("models")]
    root = Path("/projet")
    assert datasets.layer_of("models/staging/stg_orders.sql", paths, root) == "staging"
    assert datasets.layer_of("models/marts/x.sql", paths, root) == "marts"
    assert datasets.layer_of("models/x.sql", paths, root) == ""


@pytest.mark.parametrize(
    "input_entries, expected",
    [
        ([{"source_name": "raw", "table": "raw_orders"}], "staging"),
        ([{"ref": "raw_customers"}], "staging"),
        ([{"ref": "stg_orders"}], "intermediate"),
        ([{"ref": "stg_orders"}, {"ref": "raw_customers"}], "staging"),
        ([{"ref": "customer_orders"}], "marts"),
        ([], "staging"),
    ],
)
def test_la_couche_est_suggeree_selon_la_nature_des_entrees(
    settings: Settings, input_entries: list[dict], expected: str
):
    manifest = FakeManifest(
        nodes=[
            fake_node(
                "model.demo.stg_orders",
                "stg_orders",
                path="models/staging/stg_orders.sql",
            ),
            fake_node(
                "model.demo.customer_orders",
                "customer_orders",
                path="models/marts/customer_orders.sql",
            ),
            fake_node(
                "seed.demo.raw_customers",
                "raw_customers",
                resource_type="seed",
                path="seeds/raw_customers.csv",
            ),
        ]
    )
    out = datasets.suggest_layer(settings, manifest, input_entries)
    assert out["layer"] == expected
    assert isinstance(out["reason"], str)


def test_les_couches_reprennent_la_materialisation_du_projet(settings: Settings):
    layers_list = {c["name"]: c for c in datasets.layers(settings)}
    assert layers_list["staging"]["materialized"] == "view"
    assert layers_list["marts"]["materialized"] == "table"
    assert layers_list["staging"]["exists"] is True
    assert layers_list["intermediate"]["exists"] is False
    assert "" in layers_list, "on doit pouvoir ranger un modèle à la racine"


# --------------------------------------------------- fraîcheur des sources


def test_la_fraicheur_se_relit_dans_sources_json(settings, project_dir):
    """`dbt source freshness` écrit un artefact : l'atelier doit savoir le lire."""
    import json

    from pliq.dbt_service import DbtService

    target = project_dir / "target"
    target.mkdir(exist_ok=True)
    (target / "sources.json").write_text(
        json.dumps(
            {
                "metadata": {"generated_at": "2026-01-02T03:04:05Z"},
                "results": [
                    {
                        "unique_id": "source.demo.raw.orders",
                        "status": "warn",
                        "max_loaded_at": "2026-01-01T00:00:00+00:00",
                        "max_loaded_at_time_ago_in_s": 97445.0,
                        "criteria": {"warn_after": {"count": 12, "period": "hour"}},
                    },
                    {"unique_id": None, "status": "pass"},
                ],
            }
        )
    )
    svc = DbtService(settings)
    svc.load_freshness()

    assert set(svc.freshness) == {"source.demo.raw.orders"}
    state = svc.freshness["source.demo.raw.orders"]
    assert state["status"] == "warn"
    assert state["age_seconds"] == 97445.0
    assert state["checked_at"] == "2026-01-02T03:04:05Z"


def test_une_source_ne_prend_pas_le_statut_d_un_run(settings, project_dir):
    """`source freshness` écrit aussi dans run_results.json.

    Une source n'est jamais construite : reprendre son résultat peignait la
    pastille de statut du Flow avec l'état de fraîcheur, sous un nom qui ne lui
    va pas — et on lisait deux fois la même chose.
    """
    import json

    from pliq.dbt_service import DbtService

    target = project_dir / "target"
    target.mkdir(exist_ok=True)
    (target / "run_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {"unique_id": "source.demo.raw.orders", "status": "error"},
                    {"unique_id": "model.demo.stg_orders", "status": "success"},
                ]
            }
        )
    )
    svc = DbtService(settings)
    svc.load_run_results()
    assert set(svc.node_state) == {"model.demo.stg_orders"}


def test_la_commande_de_fraicheur_est_traduite_pour_dbt():
    """L'atelier dit « freshness » ; dbt veut « source freshness »."""
    from pliq.dbt_service import COMMAND_ARGS

    assert COMMAND_ARGS["freshness"] == ["source", "freshness"]

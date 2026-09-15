"""Écriture des fichiers du projet dbt.

Deux règles à tenir : ne jamais écrire hors du projet, et ne jamais réécrire un
YAML existant à la machine — commentaires, ordre et guillemets doivent survivre.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pliq import files
from pliq.config import Settings
from pliq.files import FileError

from .conftest import write

# --------------------------------------------------------------- chemins


@pytest.mark.parametrize(
    "hostile",
    [
        "../evade.sql",
        "models/../../evade.sql",
        "/etc/passwd",
        "",
    ],
)
def test_on_n_ecrit_jamais_hors_du_projet(settings: Settings, hostile: str):
    with pytest.raises(FileError):
        files.safe_path(settings, hostile)


def test_chemin_interne_accepte(settings: Settings):
    p = files.safe_path(settings, "models/staging/stg_orders.sql")
    assert p.parent.name == "staging"


def test_ecriture_cree_les_dossiers_et_termine_par_un_saut_de_ligne(settings: Settings):
    files.write_text(settings, "models/marts/neuf/modele.sql", "select 1")
    assert (
        settings.project_dir / "models/marts/neuf/modele.sql"
    ).read_text() == "select 1\n"


def test_lecture_d_un_fichier_absent(settings: Settings):
    with pytest.raises(FileError, match="introuvable"):
        files.read_text(settings, "models/inconnu.sql")


def test_schema_voisin_du_modele():
    assert (
        files.schema_file_for("models/marts/customer_orders.sql")
        == "models/marts/schema.yml"
    )


# ---------------------------------------------------- documentation modèle


EXISTING_SCHEMA = """\
version: 2

models:
  # ce commentaire doit survivre
  - name: stg_customers
    description: "Clients nettoyés"
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
"""


def _written_yaml(settings: Settings, rel: str) -> dict:
    return yaml.safe_load((settings.project_dir / rel).read_text())


def args_of(body: dict) -> dict:
    """Les arguments d'un test écrit, quelle que soit la syntaxe employée.

    dbt ne comprend `arguments:` qu'à partir de 1.10.5, et Pliq écrit donc la
    forme que le dbt installé accepte. Un test qui exigerait l'une des deux
    échouerait sur la moitié des versions que `pyproject.toml` annonce.
    """
    if "arguments" in body:
        return dict(body["arguments"])
    return {k: v for k, v in body.items() if k != "config"}


def test_documenter_un_modele_dans_un_fichier_neuf(settings: Settings):
    files.upsert_doc(
        settings,
        "models/marts/schema.yml",
        "customer_orders",
        description="Une ligne par client",
        columns=[
            {
                "name": "customer_id",
                "description": "Clé du client",
                "tests": [{"name": "unique"}, {"name": "not_null"}],
            }
        ],
    )
    data = _written_yaml(settings, "models/marts/schema.yml")
    assert data["version"] == 2
    model = data["models"][0]
    assert model["name"] == "customer_orders"
    assert model["description"] == "Une ligne par client"
    assert model["columns"][0]["tests"] == ["unique", "not_null"]


def test_le_reste_du_fichier_n_est_pas_touche(settings: Settings):
    rel = "models/staging/schema.yml"
    write(settings.project_dir / rel, EXISTING_SCHEMA)
    files.upsert_doc(settings, rel, "stg_orders", description="Commandes")
    text = (settings.project_dir / rel).read_text()
    assert "# ce commentaire doit survivre" in text
    data = yaml.safe_load(text)
    assert [m["name"] for m in data["models"]] == ["stg_customers", "stg_orders"]
    assert data["models"][0]["columns"][0]["tests"] == ["unique", "not_null"]


def test_documenter_deux_fois_ne_duplique_pas(settings: Settings):
    rel = "models/marts/schema.yml"
    for description in ("v1", "v2"):
        files.upsert_doc(settings, rel, "customer_orders", description=description)
    data = _written_yaml(settings, rel)
    assert len(data["models"]) == 1
    assert data["models"][0]["description"] == "v2"


def test_la_convention_data_tests_du_fichier_est_respectee(settings: Settings):
    rel = "models/marts/schema.yml"
    write(
        settings.project_dir / rel,
        """\
version: 2
models:
  - name: deja_la
    columns:
      - name: id
        data_tests:
          - unique
""",
    )
    files.upsert_doc(
        settings,
        rel,
        "customer_orders",
        columns=[{"name": "customer_id", "tests": [{"name": "not_null"}]}],
    )
    data = _written_yaml(settings, rel)
    new = next(m for m in data["models"] if m["name"] == "customer_orders")
    assert "data_tests" in new["columns"][0]
    assert "tests" not in new["columns"][0]


def test_documenter_une_source_au_tables_illisible_est_refuse(settings: Settings):
    """Le pendant de `datasets.declare_source` : `_source_table` faisait le
    même `.append()` sur un `tables:` d'une autre forme, donc la même panne."""
    rel = "models/staging/sources.yml"
    write(
        settings.project_dir / rel,
        """\
version: 2

sources:
  - name: raw
    schema: main
    tables: raw_orders
""",
    )
    with pytest.raises(FileError, match="n'est pas une liste"):
        files.upsert_doc(
            settings,
            rel,
            "raw_orders",
            kind="source",
            source_name="raw",
            description="Commandes brutes",
        )
    assert "tables: raw_orders" in (settings.project_dir / rel).read_text()


def test_tests_parametres_et_severite(settings: Settings):
    rel = "models/marts/schema.yml"
    files.upsert_doc(
        settings,
        rel,
        "customer_orders",
        columns=[
            {
                "name": "country_code",
                "tests": [
                    {
                        "name": "accepted_values",
                        "values": ["FR", "ES"],
                        "severity": "warn",
                    },
                    {
                        "name": "relationships",
                        "to": "stg_customers",
                        "field": "customer_id",
                    },
                    {"name": "not_null", "severity": "warn"},
                ],
            }
        ],
    )
    tests = _written_yaml(settings, rel)["models"][0]["columns"][0]["tests"]
    accepted = tests[0]["accepted_values"]
    assert args_of(accepted)["values"] == ["FR", "ES"]
    assert accepted["config"]["severity"] == "warn"
    assert args_of(tests[1]["relationships"])["to"] == "ref('stg_customers')"
    assert tests[2]["not_null"]["config"]["severity"] == "warn"


def test_un_test_incomplet_est_refuse_a_l_ecriture(settings: Settings):
    rel = "models/marts/schema.yml"
    with pytest.raises(FileError, match="valeurs autorisées"):
        files.upsert_doc(
            settings,
            rel,
            "m",
            columns=[
                {"name": "c", "tests": [{"name": "accepted_values", "values": []}]}
            ],
        )
    with pytest.raises(FileError, match="relation"):
        files.upsert_doc(
            settings,
            rel,
            "m",
            columns=[{"name": "c", "tests": [{"name": "relationships", "to": ""}]}],
        )


def test_retirer_les_tests_d_une_colonne(settings: Settings):
    rel = "models/marts/schema.yml"
    files.upsert_doc(
        settings,
        rel,
        "m",
        columns=[{"name": "c", "description": "gardée", "tests": [{"name": "unique"}]}],
    )
    files.upsert_doc(
        settings,
        rel,
        "m",
        columns=[{"name": "c", "description": "gardée", "tests": []}],
    )
    column = _written_yaml(settings, rel)["models"][0]["columns"][0]
    assert column == {"name": "c", "description": "gardée"}


def test_une_colonne_sans_rien_a_dire_n_encombre_pas_le_yaml(settings: Settings):
    rel = "models/marts/schema.yml"
    files.upsert_doc(
        settings,
        rel,
        "m",
        description="x",
        columns=[{"name": "c", "description": "", "tests": []}],
    )
    assert "columns" not in _written_yaml(settings, rel)["models"][0]


def test_les_tags_d_un_modele_vont_dans_config(settings: Settings):
    rel = "models/marts/schema.yml"
    files.upsert_doc(settings, rel, "m", tags=["finance", " "])
    assert _written_yaml(settings, rel)["models"][0]["config"]["tags"] == ["finance"]
    files.upsert_doc(settings, rel, "m", tags=[])
    assert "config" not in _written_yaml(settings, rel)["models"][0]


def test_documenter_un_seed(settings: Settings):
    files.upsert_doc(
        settings,
        "seeds/schema.yml",
        "raw_orders",
        kind="seed",
        description="CSV de référence",
        tags=["brut"],
    )
    data = _written_yaml(settings, "seeds/schema.yml")
    assert data["seeds"][0]["description"] == "CSV de référence"
    assert (
        "models" not in data
    ), "un fichier de seeds ne doit pas gagner un models: vide"


def test_type_de_ressource_non_documentable(settings: Settings):
    with pytest.raises(FileError, match="non documentable"):
        files.upsert_doc(settings, "models/schema.yml", "x", kind="exposure")


# ------------------------------------------------------------------ sources


SOURCES = """\
version: 2

sources:
  - name: raw
    schema: main
    tables:
      - name: raw_orders
"""


def test_documenter_une_table_de_source(settings: Settings):
    rel = "models/staging/sources.yml"
    write(settings.project_dir / rel, SOURCES)
    files.upsert_doc(
        settings,
        rel,
        "raw_orders",
        kind="source",
        source_name="raw",
        description="Commandes brutes",
        tags=["brut"],
        columns=[{"name": "order_id", "tests": [{"name": "unique"}]}],
    )
    table = _written_yaml(settings, rel)["sources"][0]["tables"][0]
    assert table["description"] == "Commandes brutes"
    assert table["tags"] == ["brut"], "une source porte ses tags au premier niveau"
    assert table["columns"][0]["tests"] == ["unique"]


def test_source_non_declaree(settings: Settings):
    rel = "models/staging/sources.yml"
    write(settings.project_dir / rel, SOURCES)
    with pytest.raises(FileError, match="n'est pas déclarée"):
        files.upsert_doc(
            settings, rel, "t", kind="source", source_name="autre", description="x"
        )


# ------------------------------------------------------------------ lecture


def test_relire_ce_qu_on_a_ecrit(settings: Settings):
    rel = "models/marts/schema.yml"
    specs = [
        {"name": "unique", "severity": "error"},
        {"name": "accepted_values", "values": ["FR"], "severity": "warn"},
        {
            "name": "relationships",
            "to": "stg_customers",
            "field": "customer_id",
            "severity": "error",
        },
    ]
    files.upsert_doc(
        settings,
        rel,
        "m",
        description="d",
        columns=[{"name": "c", "description": "col", "tests": specs}],
    )
    input_entry = files.read_doc(settings, rel, "m")
    assert input_entry["description"] == "d"
    assert files.parse_column_tests(input_entry["columns"][0]) == specs


def test_lire_l_ancienne_syntaxe_des_tests(settings: Settings):
    """Avant dbt 1.10 les arguments n'étaient pas sous `arguments:`."""
    column = {
        "name": "c",
        "tests": [
            {
                "accepted_values": {
                    "values": ["FR", "ES"],
                    "config": {"severity": "warn"},
                }
            },
            {"relationships": {"to": "ref('stg_customers')", "field": "id"}},
            "not_null",
        ],
    }
    assert files.parse_column_tests(column) == [
        {"name": "accepted_values", "values": ["FR", "ES"], "severity": "warn"},
        {
            "name": "relationships",
            "to": "stg_customers",
            "field": "id",
            "severity": "error",
        },
        {"name": "not_null", "severity": "error"},
    ]


def test_lire_une_fiche_absente_rend_un_dictionnaire_vide(settings: Settings):
    assert files.read_doc(settings, "models/nexistepas.yml", "m") == {}
    assert files.read_doc(settings, "../dehors.yml", "m") == {}


def test_lire_un_yaml_casse_ne_fait_pas_planter_l_ecran(settings: Settings):
    rel = "models/marts/schema.yml"
    write(settings.project_dir / rel, "models: [ {")
    assert files.read_doc(settings, rel, "m") == {}


def test_les_tags_de_config_remontent_au_premier_niveau_a_la_lecture(
    settings: Settings,
):
    rel = "models/marts/schema.yml"
    files.upsert_doc(settings, rel, "m", tags=["finance"])
    assert files.read_doc(settings, rel, "m")["tags"] == ["finance"]


# ------------------------------------------------------------ YAML génériques


def test_trouver_les_fichiers_qui_declarent_une_cle(settings: Settings):
    write(settings.project_dir / "models/staging/sources.yml", SOURCES)
    write(settings.project_dir / "models/marts/schema.yml", "version: 2\nmodels: []\n")
    write(settings.project_dir / "models/staging/vide.yml", "sources: []\n")
    found = dict(files.find_yaml_with(settings, "sources", settings.model_paths()))
    assert set(found) == {"models/staging/sources.yml", "models/staging/vide.yml"}


def test_un_yaml_casse_ne_bloque_pas_la_recherche(settings: Settings):
    write(settings.project_dir / "models/staging/casse.yml", "sources: [ {")
    write(settings.project_dir / "models/staging/sources.yml", SOURCES)
    found = dict(files.find_yaml_with(settings, "sources", settings.model_paths()))
    assert set(found) == {"models/staging/sources.yml"}


def test_charger_et_ecrire_un_yaml_quelconque(settings: Settings):
    assert files.load_yaml(settings, "models/absent.yml") == {}
    files.dump_yaml(settings, "models/neuf.yml", {"version": 2, "sources": []})
    assert files.load_yaml(settings, "models/neuf.yml")["version"] == 2


# ------------------------------- régression : l'aller-retour ne dégrade rien

SCHEMA_RICH_TESTS = """\
version: 2

models:
  - name: stg_orders
    columns:
      - name: amount_eur
        description: "Montant"
        data_tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 100000
              config:
                where: "active = true"
                severity: warn
          - not_null:
              config:
                where: "status != 'draft'"
          - accepted_values:
              arguments:
                values: ["a", "b"]
                quote: false
"""


def test_documenter_une_colonne_preserve_ses_tests_existants(settings: Settings):
    """L'interface n'édite qu'une poignée de propriétés ; le reste doit rester.

    Relire le YAML puis le réécrire perdait les arguments des tests
    personnalisés et les `config.where` — documenter une colonne dégradait
    silencieusement la qualité de données du projet.
    """
    rel = "models/staging/schema.yml"
    write(settings.project_dir / rel, SCHEMA_RICH_TESTS)

    column = _written_yaml(settings, rel)["models"][0]["columns"][0]
    specs = files.parse_column_tests(column)

    # L'utilisateur ne change qu'une description : les tests repartent tels
    # que l'interface les a lus.
    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        columns=[{"name": "amount_eur", "description": "Montant TTC", "tests": specs}],
    )

    tests = _written_yaml(settings, rel)["models"][0]["columns"][0]["data_tests"]
    by_name = {files.test_name_of(t): t for t in tests}

    range_ = by_name["dbt_utils.accepted_range"]["dbt_utils.accepted_range"]
    assert range_["arguments"] == {"min_value": 0, "max_value": 100000}
    assert range_["config"]["where"] == "active = true"
    assert range_["config"]["severity"] == "warn"

    assert by_name["not_null"]["not_null"]["config"]["where"] == "status != 'draft'"
    assert by_name["accepted_values"]["accepted_values"]["arguments"]["quote"] is False


def test_l_interface_edite_bien_ce_qu_elle_connait(settings: Settings):
    """Préserver l'inconnu ne doit pas empêcher de modifier le connu."""
    rel = "models/staging/schema.yml"
    write(settings.project_dir / rel, SCHEMA_RICH_TESTS)
    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        columns=[
            {
                "name": "amount_eur",
                "tests": [
                    {
                        "name": "accepted_values",
                        "severity": "error",
                        "values": ["a", "b", "c"],
                    }
                ],
            }
        ],
    )
    tests = _written_yaml(settings, rel)["models"][0]["columns"][0]["data_tests"]
    # Le test retiré dans l'interface disparaît, celui qui reste est à jour…
    assert [files.test_name_of(t) for t in tests] == ["accepted_values"]
    body = tests[0]["accepted_values"]
    assert body["arguments"]["values"] == ["a", "b", "c"]
    # …sans que `severity: warn` s'invente, ni que `quote` se perde.
    assert body["arguments"]["quote"] is False
    assert "config" not in body


def test_un_test_ancienne_syntaxe_garde_sa_forme(settings: Settings):
    """Des arguments au premier niveau ne sont pas convertis d'autorité."""
    rel = "models/staging/schema.yml"
    write(
        settings.project_dir / rel,
        """\
version: 2
models:
  - name: stg_orders
    columns:
      - name: status
        tests:
          - accepted_values:
              values: ["a"]
              quote: true
""",
    )
    column = _written_yaml(settings, rel)["models"][0]["columns"][0]
    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        columns=[{"name": "status", "tests": files.parse_column_tests(column)}],
    )
    body = _written_yaml(settings, rel)["models"][0]["columns"][0]["tests"][0]
    assert body["accepted_values"] == {"values": ["a"], "quote": True}


def test_un_test_ajoute_reste_en_forme_courte(settings: Settings):
    """Rien à préserver : on ne transforme pas `unique` en bloc verbeux."""
    rel = "models/marts/schema.yml"
    files.upsert_doc(
        settings,
        rel,
        "customer_orders",
        columns=[{"name": "customer_id", "tests": [{"name": "unique"}]}],
    )
    assert _written_yaml(settings, rel)["models"][0]["columns"][0]["tests"] == [
        "unique"
    ]


def test_un_arguments_illisible_ne_fait_pas_planter_la_sauvegarde(settings: Settings):
    """Le YAML vient de l'utilisateur : `arguments:` peut être n'importe quoi."""
    rel = "models/staging/schema.yml"
    write(
        settings.project_dir / rel,
        """\
version: 2
models:
  - name: stg_orders
    columns:
      - name: status
        data_tests:
          - accepted_values:
              arguments: "pas un dictionnaire"
""",
    )
    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        columns=[
            {
                "name": "status",
                "tests": [{"name": "accepted_values", "values": ["a"]}],
            }
        ],
    )
    body = _written_yaml(settings, rel)["models"][0]["columns"][0]["data_tests"][0]
    assert args_of(body["accepted_values"]) == {"values": ["a"]}


def test_reenregistrer_plusieurs_colonnes_deja_testees(settings: Settings):
    """Le `entry` de la boucle des tests écrasait celui de la ressource.

    Avec un test en forme courte (`- unique`), `entry` devenait la chaîne
    « unique » et la colonne suivante levait un AttributeError ; avec un test
    en dictionnaire, elle était écrite dans le test au lieu du modèle et les
    modifications disparaissaient sans bruit.
    """
    rel = "models/staging/schema.yml"
    write(
        settings.project_dir / rel,
        """\
version: 2
models:
  - name: stg_orders
    columns:
      - name: order_id
        tests:
          - unique
      - name: status
        description: "Ancienne description"
      - name: amount_eur
        description: "Ancien montant"
""",
    )

    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        columns=[
            {"name": "order_id", "tests": [{"name": "unique"}]},
            {"name": "status", "description": "Nouvelle description"},
            {"name": "amount_eur", "description": "Nouveau montant"},
        ],
    )

    by_name = {
        c["name"]: c for c in _written_yaml(settings, rel)["models"][0]["columns"]
    }
    assert by_name["order_id"]["tests"] == ["unique"]
    assert by_name["status"]["description"] == "Nouvelle description"
    assert by_name["amount_eur"]["description"] == "Nouveau montant"


def test_reenregistrer_apres_un_test_en_dictionnaire(settings: Settings):
    """Même piège, mais silencieux : le test est un dict, rien ne plante."""
    rel = "models/staging/schema.yml"
    write(
        settings.project_dir / rel,
        """\
version: 2
models:
  - name: stg_orders
    description: "Commandes"
    columns:
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ["completed"]
      - name: amount_eur
        description: "Ancien montant"
""",
    )

    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        description="Commandes nettoyées",
        columns=[
            {
                "name": "status",
                "tests": [{"name": "accepted_values", "values": ["completed"]}],
            },
            {"name": "amount_eur", "description": "Nouveau montant"},
        ],
    )

    model = _written_yaml(settings, rel)["models"][0]
    assert model["description"] == "Commandes nettoyées"
    by_name = {c["name"]: c for c in model["columns"]}
    assert by_name["amount_eur"]["description"] == "Nouveau montant"
    assert "columns" not in model["columns"][0], "le test ne doit rien avoir reçu"


# ------------------------------------------------- relations : formes de `ref`


@pytest.mark.parametrize(
    "expression",
    [
        "source('raw', 'customers')",
        "ref('mon_paquet', 'customers')",
        "ref('customers', v=2)",
    ],
)
def test_une_relation_qui_n_est_pas_un_ref_simple_survit_a_l_aller_retour(
    settings: Settings, expression: str
):
    """Écrire la doc ne doit pas casser un test qu'on n'a pas touché.

    La lecture ne reconnaissait que `ref('modele')` et rendait le reste tel
    quel dans `to` ; l'écriture le renveloppait, et produisait
    `ref("source('raw', 'customers')")`, que dbt ne compile plus.
    """
    rel = "models/staging/schema.yml"
    write(
        settings.project_dir / rel,
        f"""\
version: 2
models:
  - name: stg_orders
    columns:
      - name: customer_id
        tests:
          - relationships:
              to: {expression}
              field: customer_id
""",
    )
    column = _written_yaml(settings, rel)["models"][0]["columns"][0]
    read_values = files.parse_column_tests(column)
    assert read_values[0]["to"] == "", "l'interface n'a pas de modèle à proposer"
    assert read_values[0]["to_expr"] == expression

    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        description="Commandes",
        columns=[
            {"name": "customer_id", "description": "Client", "tests": read_values}
        ],
    )
    written = _written_yaml(settings, rel)["models"][0]["columns"][0]["tests"][0]
    assert args_of(written["relationships"])["to"] == expression


def test_choisir_un_modele_remplace_l_expression_conservee(settings: Settings):
    """L'expression est préservée, pas figée : l'interface reprend la main."""
    rel = "models/staging/schema.yml"
    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        columns=[
            {
                "name": "customer_id",
                "tests": [
                    {
                        "name": "relationships",
                        "to": "stg_customers",
                        "to_expr": "source('raw', 'customers')",
                        "field": "customer_id",
                    }
                ],
            }
        ],
    )
    written = _written_yaml(settings, rel)["models"][0]["columns"][0]["tests"][0]
    assert args_of(written["relationships"])["to"] == "ref('stg_customers')"


def test_une_relation_sans_cible_est_refusee(settings: Settings):
    with pytest.raises(files.FileError, match="relation"):
        files.upsert_doc(
            settings,
            "models/staging/schema.yml",
            "stg_orders",
            columns=[{"name": "customer_id", "tests": [{"name": "relationships"}]}],
        )


# -------------------------------------------- syntaxe des arguments et version


def test_la_syntaxe_des_arguments_suit_le_dbt_installe(monkeypatch):
    """`arguments:` n'existe qu'à partir de dbt 1.10.5.

    `pyproject.toml` accepte dbt à partir de 1.9 : écrire la syntaxe récente
    dans un environnement 1.9 produit un test que dbt refuse de compiler
    (« macro takes no keyword argument 'arguments' »).
    """
    monkeypatch.setattr(files, "dbt_version", lambda: (1, 9, 0))
    body = files._test_entry({"name": "accepted_values", "values": ["a"]}, "tests")
    assert body == {"accepted_values": {"values": ["a"]}}

    monkeypatch.setattr(files, "dbt_version", lambda: (1, 10, 5))
    body = files._test_entry({"name": "accepted_values", "values": ["a"]}, "tests")
    assert body == {"accepted_values": {"arguments": {"values": ["a"]}}}


def test_la_version_de_dbt_se_lit_et_se_compare():
    installed = files.dbt_version()
    assert installed and installed[0] >= 1
    assert files.dbt_version() >= (1, 9)


def test_validation_avant_ecriture(settings: Settings):
    """Un test incomplet est refusé sans qu'aucun fichier n'ait été ouvert."""
    with pytest.raises(files.FileError, match="valeurs autorisées"):
        files.validate_columns(
            [{"name": "status", "tests": [{"name": "accepted_values", "values": []}]}]
        )
    assert not (settings.project_dir / "models/staging/schema.yml").exists()


@pytest.mark.parametrize(
    "hostile",
    ["x') or ref('y", "x'); drop table t --", "mon modèle", "2eme", ""],
)
def test_une_cible_de_relation_fabriquee_est_refusee(hostile: str):
    """`to` est recollé dans du Jinja : il doit être un nom de modèle.

    L'interface ne propose que des noms du manifeste, mais l'API accepte ce
    qu'on lui donne — et `ref('x') or ref('y')` refermait le `ref()` pour en
    ouvrir un autre.
    """
    with pytest.raises(FileError):
        files.test_arguments({"name": "relationships", "to": hostile})


def test_une_cible_de_relation_valide_passe():
    assert files.test_arguments({"name": "relationships", "to": "stg_customers"}) == {
        "to": "ref('stg_customers')",
        "field": "id",
    }


def test_une_cible_fabriquee_est_refusee_avant_toute_ecriture(settings: Settings):
    with pytest.raises(FileError, match="nom de modèle"):
        files.validate_columns(
            [{"name": "c", "tests": [{"name": "relationships", "to": "a') or ref('b"}]}]
        )
    assert not (settings.project_dir / "models/staging/schema.yml").exists()


def test_to_expr_ne_peut_pas_venir_de_la_requete(settings: Settings):
    """Conserver une expression est un droit qui vient du disque.

    `to_expr` a d'abord contourné le contrôle posé sur `to` : une requête qui
    l'inventait faisait écrire n'importe quel Jinja. L'expression reconduite se
    lit désormais dans le fichier, jamais dans ce que la requête prétend.
    """
    rel = "models/staging/schema.yml"
    write(
        settings.project_dir / rel,
        """\
version: 2
models:
  - name: stg_orders
    columns:
      - name: customer_id
        tests:
          - relationships:
              to: source('raw', 'customers')
              field: customer_id
""",
    )
    # Ce que le fichier dit est reconduit, même si la requête ment.
    files.upsert_doc(
        settings,
        rel,
        "stg_orders",
        columns=[
            {
                "name": "customer_id",
                "tests": [{"name": "relationships", "to_expr": "ref('a') or ref('b')"}],
            }
        ],
    )
    written = _written_yaml(settings, rel)["models"][0]["columns"][0]["tests"][0]
    assert args_of(written["relationships"])["to"] == "source('raw', 'customers')"

    # Et sans rien sur le disque, il n'y a rien à conserver : on refuse.
    with pytest.raises(FileError, match="vers quoi pointer"):
        files.upsert_doc(
            settings,
            "models/marts/schema.yml",
            "neuf",
            columns=[
                {
                    "name": "c",
                    "tests": [
                        {"name": "relationships", "to_expr": "ref('a') or ref('b')"}
                    ],
                }
            ],
        )


# ------------------------------------------------- fraîcheur d'une source


def _source_with_freshness(settings: Settings, **cfg) -> dict:
    rel = "models/staging/sources.yml"
    write(settings.project_dir / rel, SOURCES)
    files.upsert_doc(
        settings, rel, "raw_orders", kind="source", source_name="raw", freshness=cfg
    )
    return _written_yaml(settings, rel)["sources"][0]["tables"][0]


def test_regler_la_fraicheur_d_une_source(settings: Settings):
    table = _source_with_freshness(
        settings,
        loaded_at_field="ingested_at",
        warn_after={"count": 12, "period": "hour"},
        error_after={"count": 2, "period": "day"},
    )
    assert table["loaded_at_field"] == "ingested_at"
    assert table["freshness"]["warn_after"] == {"count": 12, "period": "hour"}
    assert table["freshness"]["error_after"] == {"count": 2, "period": "day"}


def test_la_colonne_de_chargement_accepte_une_expression(settings: Settings):
    """dbt exige un horodatage : beaucoup de tables n'ont qu'une date.

    L'expression est la réponse documentée, et l'atelier doit pouvoir l'écrire.
    """
    table = _source_with_freshness(
        settings, loaded_at_field="cast(order_date as timestamp)"
    )
    assert table["loaded_at_field"] == "cast(order_date as timestamp)"


def test_une_colonne_de_chargement_illisible_est_refusee(settings: Settings):
    with pytest.raises(FileError, match="SQL invalide"):
        _source_with_freshness(settings, loaded_at_field="cast(order_date as")


def test_un_seuil_sans_colonne_de_chargement_est_refuse(settings: Settings):
    """Sans elle, dbt saute la source : la fraîcheur n'est pas bonne, elle est
    inconnue — et rien dans le Flow ne le dirait."""
    with pytest.raises(FileError, match="ne mesure rien"):
        _source_with_freshness(settings, warn_after={"count": 1, "period": "day"})


def test_une_unite_de_fraicheur_inconnue_est_refusee(settings: Settings):
    with pytest.raises(FileError, match="Unité de fraîcheur"):
        _source_with_freshness(
            settings, loaded_at_field="ts", warn_after={"count": 1, "period": "semaine"}
        )


def test_un_seuil_de_fraicheur_doit_etre_un_nombre_positif(settings: Settings):
    for wrong in ({"count": 0, "period": "day"}, {"count": "deux", "period": "day"}):
        with pytest.raises(FileError, match="Seuil de fraîcheur"):
            _source_with_freshness(settings, loaded_at_field="ts", warn_after=wrong)


def test_retirer_la_fraicheur_nettoie_le_yaml(settings: Settings):
    rel = "models/staging/sources.yml"
    write(settings.project_dir / rel, SOURCES)
    shared = {"kind": "source", "source_name": "raw"}
    files.upsert_doc(
        settings,
        rel,
        "raw_orders",
        **shared,
        freshness={
            "loaded_at_field": "ts",
            "warn_after": {"count": 1, "period": "day"},
        },
    )
    files.upsert_doc(settings, rel, "raw_orders", **shared, freshness={})
    table = _written_yaml(settings, rel)["sources"][0]["tables"][0]
    assert "loaded_at_field" not in table and "freshness" not in table


# --- héritage du groupe, bloc `config:`, désactivation explicite -------------
#
# dbt ne lit pas `freshness` et `loaded_at_field` au seul endroit où l'atelier
# les écrivait. Une table en hérite de son groupe `sources:`, et les deux
# propriétés vivent aussi sous `config:` depuis dbt 1.10. `resolved_freshness`
# est ce que dbt applique aujourd'hui — le serveur le tient du manifeste.

SOURCES_FRESH_GROUP = """\
version: 2

sources:
  - name: raw
    schema: main
    loaded_at_field: ts
    freshness:
      warn_after: {count: 1, period: hour}
    tables:
      - name: raw_orders
"""

SOURCES_UNDER_CONFIG = """\
version: 2

sources:
  - name: raw
    schema: main
    tables:
      - name: raw_orders
        config:
          loaded_at_field: ts
          freshness:
            warn_after: {count: 1, period: hour}
"""

RESOLVED_1H = {"loaded_at_field": "ts", "warn_after": {"count": 1, "period": "hour"}}


def _apply_setting(
    settings: Settings, yaml_source: str, cfg: dict, resolved=RESOLVED_1H
) -> dict:
    rel = "models/staging/sources.yml"
    write(settings.project_dir / rel, yaml_source)
    files.upsert_doc(
        settings,
        rel,
        "raw_orders",
        kind="source",
        source_name="raw",
        freshness=cfg,
        resolved_freshness=resolved,
    )
    return _written_yaml(settings, rel)["sources"][0]["tables"][0]


def test_modifier_un_seuil_herite_du_groupe_l_ecrit_sur_la_table(settings: Settings):
    """Le seuil affiché était vide, et le nouveau n'atteignait jamais dbt."""
    table = _apply_setting(
        settings,
        SOURCES_FRESH_GROUP,
        {"loaded_at_field": "ts", "warn_after": {"count": 48, "period": "hour"}},
    )
    assert table["freshness"]["warn_after"] == {"count": 48, "period": "hour"}
    # La colonne, elle, n'a pas changé : inutile de recopier l'héritage.
    assert "loaded_at_field" not in table


def test_vider_un_seuil_herite_le_desactive_explicitement(settings: Settings):
    """Retirer l'entrée de la table ne suffit pas : le groupe continuerait.

    dbt n'annule un héritage que sur une valeur nulle écrite pour la table.
    """
    table = _apply_setting(settings, SOURCES_FRESH_GROUP, {"loaded_at_field": "ts"})
    assert "freshness" in table and table["freshness"] is None


def test_un_reglage_inchange_ne_touche_pas_le_yaml(settings: Settings):
    """Rendre le seuil qu'on vient d'afficher ne doit rien matérialiser."""
    rel = "models/staging/sources.yml"
    write(settings.project_dir / rel, SOURCES_FRESH_GROUP)
    before = (settings.project_dir / rel).read_text()
    files.upsert_doc(
        settings,
        rel,
        "raw_orders",
        kind="source",
        source_name="raw",
        freshness=dict(RESOLVED_1H),
        resolved_freshness=RESOLVED_1H,
    )
    assert (settings.project_dir / rel).read_text() == before


def test_l_ecriture_suit_le_bloc_config_de_la_table(settings: Settings):
    """`config:` l'emporte sur le premier niveau : y écrire est le seul effet utile."""
    table = _apply_setting(
        settings,
        SOURCES_UNDER_CONFIG,
        {"loaded_at_field": "ts", "warn_after": {"count": 48, "period": "hour"}},
    )
    assert table["config"]["freshness"]["warn_after"] == {"count": 48, "period": "hour"}
    assert "freshness" not in table, "rien au premier niveau, que dbt ignorerait"


def test_vider_un_seuil_propre_nettoie_le_yaml(settings: Settings):
    """Sans rien à recouvrir, le retrait suffit : pas de `null` inutile."""
    table = _apply_setting(
        settings,
        """\
version: 2

sources:
  - name: raw
    schema: main
    tables:
      - name: raw_orders
        loaded_at_field: ts
        freshness:
          warn_after: {count: 1, period: hour}
""",
        {"loaded_at_field": "ts"},
    )
    assert "freshness" not in table


def test_le_filter_de_dbt_survit_a_une_modification_de_seuil(settings: Settings):
    """`filter` appartient au projet : l'atelier ne le règle pas, et ne l'efface pas."""
    table = _apply_setting(
        settings,
        """\
version: 2

sources:
  - name: raw
    schema: main
    tables:
      - name: raw_orders
        loaded_at_field: ts
        freshness:
          filter: "jour > '2020-01-01'"
          warn_after: {count: 1, period: hour}
""",
        {"loaded_at_field": "ts", "warn_after": {"count": 48, "period": "hour"}},
    )
    assert table["freshness"]["filter"] == "jour > '2020-01-01'"
    assert table["freshness"]["warn_after"] == {"count": 48, "period": "hour"}


def test_la_fraicheur_ne_se_regle_que_sur_une_source(settings: Settings):
    with pytest.raises(FileError, match="table de source"):
        files.upsert_doc(
            settings,
            "models/marts/schema.yml",
            "m",
            kind="model",
            freshness={"loaded_at_field": "ts"},
        )


# ------------------------------------------------ renommer dans les fichiers


SCHEMA_TO_RENAME = """\
version: 2

models:
  # Le modèle des commandes, commenté à la main.
  - name: stg_orders
    description: "Commandes typées"
    columns:
      - name: order_id
        tests:
          - unique
          - not_null
          - relationships:
              to: ref('stg_orders')
              field: order_id

  - name: autre
    description: "À ne pas confondre"
"""


def test_renommer_un_modele_garde_sa_place_et_ses_commentaires(settings: Settings):
    """Retirer l'entrée pour la réécrire la renverrait en fin de fichier, sans
    ses commentaires et sans les tests que l'interface ne sait pas éditer."""
    write(settings.project_dir / "models/staging/schema.yml", SCHEMA_TO_RENAME)

    assert files.rename_doc(
        settings, "models/staging/schema.yml", "stg_orders", "commandes"
    )

    text = (settings.project_dir / "models/staging/schema.yml").read_text()
    data = yaml.safe_load(text)
    assert [m["name"] for m in data["models"]] == ["commandes", "autre"]
    assert "# Le modèle des commandes, commenté à la main." in text
    assert data["models"][0]["columns"][0]["tests"][:2] == ["unique", "not_null"]


def test_renommer_ce_qui_n_est_pas_documente_ne_dit_pas_le_contraire(
    settings: Settings,
):
    write(settings.project_dir / "models/staging/schema.yml", SCHEMA_TO_RENAME)
    assert not files.rename_doc(
        settings, "models/staging/schema.yml", "jamais_vu", "neuf"
    )
    assert not files.rename_doc(settings, "models/marts/schema.yml", "a", "b")


def test_les_ref_du_projet_sont_retrouves_puis_suivis(settings: Settings):
    """Un `ref()` ne vit pas que dans models/ : un test singulier en porte un,
    et un test de relation le nomme depuis un YAML."""
    root = settings.project_dir
    write(root / "models/marts/aval.sql", "select * from {{ ref('stg_orders') }}\n")
    write(
        root / "models/staging/schema.yml",
        "version: 2\nmodels:\n  - name: autre\n    columns:\n"
        "      - name: id\n        tests:\n          - relationships:\n"
        "              to: ref('stg_orders')\n              field: id\n",
    )
    write(root / "tests/pas_de_trou.sql", "select 1 from {{ ref('stg_orders') }}\n")
    write(root / "models/marts/ailleurs.sql", "select * from {{ ref('autre') }}\n")

    found = files.code_files_naming(settings, "stg_orders")
    assert {p.name for p in found} == {"aval.sql", "schema.yml", "pas_de_trou.sql"}

    touches = files.rename_refs(settings, "stg_orders", "commandes", found)
    assert sorted(touches) == [
        "models/marts/aval.sql",
        "models/staging/schema.yml",
        "tests/pas_de_trou.sql",
    ]
    assert "{{ ref('commandes') }}" in (root / "models/marts/aval.sql").read_text()
    assert "ref('commandes')" in (root / "models/staging/schema.yml").read_text()
    assert "{{ ref('autre') }}" in (root / "models/marts/ailleurs.sql").read_text()


def test_un_lien_vers_un_fichier_hors_du_projet_arrete_le_renommage(
    settings: Settings, tmp_path: Path
):
    """`is_file()` suit les liens, et la réécriture aussi.

    Les contrôles posés sur le modèle renommé et sa documentation passent par
    `safe_path()`, qui résout ; ceux qui le *nomment* n'y passaient pas. Un
    `models/consumer.sql` pointant hors du projet se faisait donc réécrire, au
    mépris de la règle de confinement que l'atelier annonce.

    Le renommage entier est refusé, et non le seul fichier : sauté, il
    continuerait de nommer l'ancien modèle et dbt ne parserait plus le projet.
    """
    root = settings.project_dir
    outside_of = write(
        tmp_path / "dehors" / "partage.sql", "select {{ ref('stg_orders') }}\n"
    )
    (root / "models" / "consumer.sql").symlink_to(outside_of)

    with pytest.raises(files.FileError, match="hors"):
        files.code_files_naming(settings, "stg_orders")

    assert "ref('stg_orders')" in outside_of.read_text()


def test_un_lien_qui_reste_dans_le_projet_se_renomme(settings: Settings):
    """Le confinement ne vise pas les liens, il vise les sorties d'arbre."""
    root = settings.project_dir
    targets_it = write(
        root / "models/marts/vise.sql", "select {{ ref('stg_orders') }}\n"
    )
    (root / "models" / "lien.sql").symlink_to(targets_it)

    found = files.code_files_naming(settings, "stg_orders")
    assert {p.name for p in found} == {"vise.sql", "lien.sql"}
    files.rename_refs(settings, "stg_orders", "commandes", found)
    assert "{{ ref('commandes') }}" in targets_it.read_text()


def test_un_dossier_de_code_hors_de_l_arbre_arrete_le_renommage(
    settings: Settings, tmp_path: Path
):
    """dbt accepte `model-paths: ["../partage"]` ; l'atelier n'y écrit pas."""
    root = settings.project_dir
    write(tmp_path / "partage" / "x.sql", "select {{ ref('stg_orders') }}\n")
    yml = root / "dbt_project.yml"
    yml.write_text(yml.read_text() + '\nmodel-paths: ["../partage"]\n')

    with pytest.raises(files.FileError, match="hors du projet"):
        files.code_files_naming(settings, "stg_orders")


def test_ni_target_ni_dbt_packages_ne_sont_relus(settings: Settings):
    """Ce sont des copies que dbt refait : les réécrire ne sert à rien, et
    fouiller dbt_packages/ ferait passer le renommage sur du code d'autrui."""
    root = settings.project_dir
    write(root / "target/compiled/x.sql", "select * from {{ ref('stg_orders') }}\n")
    write(
        root / "dbt_packages/dbt_utils/models/x.sql",
        "select * from {{ ref('stg_orders') }}\n",
    )
    assert files.code_files_naming(settings, "stg_orders") == []


# --------------------------------------------------- un YAML qu'on ne lit pas
#
# `ruamel` lève sa propre famille d'exceptions. Elles traversaient les routes
# sans être reconnues : l'écran recevait un 500 sans nom de fichier, et — plus
# grave — le refus arrivait après que d'autres fichiers avaient déjà été
# écrits ou effacés.

CASING = """\
version: 2
models:
  - name: ventes
    columns: [
"""


def test_un_yaml_illisible_est_une_FileError(settings: Settings):
    write(settings.project_dir / "models/staging/schema.yml", CASING)
    with pytest.raises(FileError) as exc:
        files.remove_doc(settings, "models/staging/schema.yml", "ventes")
    assert "schema.yml" in str(exc.value), "le refus doit dire quel fichier réparer"


def test_un_yaml_illisible_est_une_FileError_aussi_a_l_ecriture(settings: Settings):
    write(settings.project_dir / "models/staging/schema.yml", CASING)
    with pytest.raises(FileError):
        files.upsert_doc(
            settings, "models/staging/schema.yml", "ventes", description="x"
        )


def test_un_yaml_qui_n_est_pas_un_dictionnaire_est_refuse(settings: Settings):
    """Une liste au premier niveau passait le parseur et cassait plus loin,
    sur un `.get()` — donc en 500, et loin du fichier fautif."""
    write(settings.project_dir / "models/staging/schema.yml", "- un\n- deux\n")
    with pytest.raises(FileError):
        files.remove_doc(settings, "models/staging/schema.yml", "ventes")


def test_check_yaml_accepte_un_fichier_sain_et_un_fichier_absent(settings: Settings):
    files.check_yaml(settings, "models/staging/schema.yml")  # absent : rien à dire
    write(settings.project_dir / "models/staging/schema.yml", "version: 2\n")
    files.check_yaml(settings, "models/staging/schema.yml")


def test_check_yaml_refuse_un_fichier_casse(settings: Settings):
    write(settings.project_dir / "models/staging/schema.yml", CASING)
    with pytest.raises(FileError):
        files.check_yaml(settings, "models/staging/schema.yml")

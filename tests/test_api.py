"""Bout en bout : un vrai projet dbt, un vrai `dbt build`, l'API HTTP.

Ces tests sont marqués `dbt` : ils lancent dbt pour de bon (quelques secondes).
La boucle rapide s'obtient avec `./test.sh -m "not dbt"`.

Le projet de test est construit une fois pour toute la session, puis chaque test
travaille dessus via l'API — c'est exactement le chemin que prend le navigateur.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import duckdb
import pytest
import yaml
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from pliq import files
from pliq import projects
from pliq import recipes as rcp
from pliq.config import Settings, load_settings
from pliq.dbt_service import DbtService
from pliq.server import create_app
from pliq.warehouse import Warehouse

from .conftest import release_duckdb_for_test, write

pytestmark = pytest.mark.dbt


RAW_CUSTOMERS = """\
customer_id,first_name,last_name,country,created_at
1,Jean,Dupont,fr,2024-01-05
2,Claire,Martin,FR,2024-01-07
3,Luis,Garcia,es,2024-02-02
"""

RAW_ORDERS = """\
order_id,customer_id,status,amount_eur,ordered_at
1,1,completed,120.50,2024-01-10
2,1,pending,30.00,2024-01-12
3,2,completed,75.25,2024-02-01
4,3,completed,10.00,2024-02-03
5,3,cancelled,99.99,2024-02-04
"""

STG_CUSTOMERS = """\
select
    customer_id,
    first_name || ' ' || last_name as full_name,
    upper(country)                 as country_code,
    cast(created_at as date)       as created_at

from {{ ref('raw_customers') }}
"""

STG_ORDERS = """\
select
    order_id,
    customer_id,
    lower(status)              as status,
    cast(amount_eur as double) as amount_eur,
    cast(ordered_at as date)   as ordered_at

from {{ ref('raw_orders') }}
"""

CUSTOMER_ORDERS = """\
{{ config(materialized='table') }}

select
    c.customer_id,
    c.full_name,
    count(o.order_id)                                          as nb_orders,
    coalesce(sum(case when o.status = 'completed'
                      then o.amount_eur end), 0)               as revenue_eur

from {{ ref('stg_customers') }} c
left join {{ ref('stg_orders') }} o
    on c.customer_id = o.customer_id
group by 1, 2
"""

SCHEMA_STAGING = """\
version: 2

models:
  - name: stg_orders
    description: "Commandes typées"
    columns:
      - name: order_id
        description: "Clé de la commande"
        tests:
          - unique
          - not_null
"""


# ----------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def project(tmp_path_factory) -> Settings:
    """Un projet dbt complet, construit une fois : seeds, staging, marts, tests."""
    root = projects.create(tmp_path_factory.mktemp("atelier"), "atelier_demo")
    write(root / "seeds" / "raw_customers.csv", RAW_CUSTOMERS)
    write(root / "seeds" / "raw_orders.csv", RAW_ORDERS)
    write(root / "models" / "staging" / "stg_customers.sql", STG_CUSTOMERS)
    write(root / "models" / "staging" / "stg_orders.sql", STG_ORDERS)
    write(root / "models" / "staging" / "schema.yml", SCHEMA_STAGING)
    write(root / "models" / "marts" / "customer_orders.sql", CUSTOMER_ORDERS)

    settings = load_settings(project_dir=str(root))
    os.chdir(root)  # dbt-duckdb résout un path: relatif depuis le cwd
    # `reset=True` : c'est un run, et il doit rendre la base avant que
    # l'atelier ouvre la sienne — DuckDB n'accepte qu'un écrivain.
    result = DbtService(settings).invoke(["build"], reset=True)
    assert result.success, "le projet de test doit se construire"
    return settings


@pytest.fixture(autouse=True)
def _in_project(project: Settings):
    """Chaque test travaille depuis le dossier du projet, comme l'atelier."""
    os.chdir(project.project_dir)


@pytest.fixture(scope="session")
def client(project: Settings):
    with TestClient(create_app(project)) as c:
        yield c


def uid_of(client: TestClient, name: str) -> str:
    flow = client.get("/api/flow").json()
    for d in flow["datasets"]:
        if d["name"] == name:
            return d["id"]
    raise AssertionError(
        f"{name} absent du Flow : {[d['name'] for d in flow['datasets']]}"
    )


def wait_for_run_end(client: TestClient, timeout: float = 180) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = client.get("/api/run").json()
        if not state["run"]["running"]:
            return state
        time.sleep(0.2)
    raise AssertionError("le run dbt ne s'est jamais terminé")


# -------------------------------------------------------------------- infos


def test_la_fiche_du_projet(client: TestClient, project: Settings):
    info = client.get("/api/project").json()
    assert info["name"] == "atelier_demo"
    assert info["adapter"] == "duckdb"
    assert info["target"] == "dev" and info["targets"] == ["dev", "prod"]
    assert info["database_exists"] is True
    assert info["manifest_error"] is None


def test_le_flow_est_biparti(client: TestClient):
    flow = client.get("/api/flow").json()
    datasets = {d["name"] for d in flow["datasets"]}
    assert {
        "raw_orders",
        "raw_customers",
        "stg_orders",
        "stg_customers",
        "customer_orders",
    } <= datasets

    recipes = {r["model_name"] for r in flow["recipes"]}
    assert recipes == {"stg_orders", "stg_customers", "customer_orders"}

    # Aucune arête ne relie deux datasets directement.
    ids = {d["id"] for d in flow["datasets"]}
    assert not any(e["from"] in ids and e["to"] in ids for e in flow["edges"])

    states = flow["states"]
    assert states[uid_of(client, "customer_orders")]["status"] == "success"


def test_le_flow_replie_ce_qu_on_lui_demande(client: TestClient):
    """Le pliage change la disposition : il se demande au serveur, pas au DOM."""
    flow = client.get("/api/flow?fold=staging").json()
    assert flow["folded"] == ["zone:staging"]

    names = {d["name"] for d in flow["datasets"]}
    assert "stg_orders" not in names and "stg_customers" not in names
    assert "staging" in names

    block = next(d for d in flow["datasets"] if d["node_type"] == "group")
    assert block["count"] == 2 and len(block["members"]) == 2
    assert {r["model_name"] for r in flow["recipes"]} == {"customer_orders"}

    # Replier l'amont, lui, se désigne depuis un nœud du graphe.
    uid = uid_of(client, "customer_orders")
    upstream = client.get(f"/api/flow?fold=up:{uid}").json()
    assert upstream["folded"] == [f"up:{uid}"]
    group = next(d for d in upstream["datasets"] if d["node_type"] == "group")
    assert group["name"] == "amont de customer_orders"
    assert [d["name"] for d in upstream["datasets"] if d["node_type"] == "dataset"] == [
        "customer_orders"
    ]

    # Sans le paramètre, rien n'est replié : le pliage ne colle pas au serveur.
    assert client.get("/api/flow").json()["folded"] == []


def test_la_fiche_d_un_modele(client: TestClient):
    d = client.get(f"/api/dataset/{uid_of(client, 'stg_orders')}").json()
    assert d["kind"] == "model" and d["layer"] == "staging"
    assert d["materialized"] == "view"
    assert d["description"] == "Commandes typées"
    assert "{{ ref('raw_orders') }}" in d["raw_sql"]
    assert "raw_orders" in d["compiled_sql"] and "{{" not in d["compiled_sql"]
    assert {t["name"] for t in d["tests"]} == {"unique", "not_null"}
    assert d["state"]["status"] == "success"


def test_un_dataset_inconnu_repond_404(client: TestClient):
    r = client.get("/api/dataset/model.atelier_demo.inexistant")
    assert r.status_code == 404 and "inconnu" in r.json()["error"]


# ------------------------------------------------------------------ explorer


def test_explorer_profile_les_colonnes(client: TestClient):
    data = client.post(
        f"/api/dataset/{uid_of(client, 'stg_customers')}/explore", json={"limit": 100}
    ).json()
    columns = {c["name"]: c for c in data["columns"]}
    assert columns["country_code"]["meaning"] == "country"
    assert columns["created_at"]["storage"] == "date"
    assert columns["customer_id"]["ok"] == 100.0
    assert len(data["rows"]) == 3 and data["total"] == 3


# --------------------------------------------------------------- documenter


def test_documenter_et_tester_un_modele(client: TestClient, project: Settings):
    uid = uid_of(client, "customer_orders")
    before = client.get(f"/api/dataset/{uid}/tests").json()
    assert before["kind"] == "model"
    assert {c["name"] for c in before["columns"]} == {
        "customer_id",
        "full_name",
        "nb_orders",
        "revenue_eur",
    }

    r = client.post(
        f"/api/dataset/{uid}/tests",
        json={
            "description": "Une ligne par client",
            "tags": ["finance"],
            "columns": [
                {
                    "name": "customer_id",
                    "description": "Clé du client",
                    "tests": [
                        {"name": "unique", "severity": "error"},
                        {"name": "not_null", "severity": "warn"},
                    ],
                }
            ],
        },
    )
    assert r.status_code == 200 and r.json()["parse_error"] is None

    after = client.get(f"/api/dataset/{uid}/tests").json()
    assert after["description"] == "Une ligne par client"
    assert after["tags"] == ["finance"]
    column = next(c for c in after["columns"] if c["name"] == "customer_id")
    assert column["description"] == "Clé du client"
    assert column["tests"] == [
        {"name": "unique", "severity": "error"},
        {"name": "not_null", "severity": "warn"},
    ]
    assert (project.project_dir / after["schema_path"]).exists()

    # Les tests écrits sont bien vus par dbt.
    flow = client.get("/api/flow?refresh=true").json()
    target = next(d for d in flow["datasets"] if d["id"] == uid)
    assert {t["name"] for t in target["tests"]} == {"unique", "not_null"}


def test_documenter_un_seed(client: TestClient):
    uid = uid_of(client, "raw_orders")
    r = client.post(
        f"/api/dataset/{uid}/tests",
        json={"description": "Commandes brutes", "columns": [], "tags": []},
    )
    assert r.status_code == 200
    node_card = client.get(f"/api/dataset/{uid}/tests").json()
    assert (
        node_card["kind"] == "seed" and node_card["description"] == "Commandes brutes"
    )
    assert node_card["schema_path"].startswith("seeds/")


def test_un_test_incomplet_est_refuse_avec_un_message(client: TestClient):
    uid = uid_of(client, "stg_orders")
    r = client.post(
        f"/api/dataset/{uid}/tests",
        json={
            "description": "",
            "columns": [
                {"name": "status", "tests": [{"name": "accepted_values", "values": []}]}
            ],
        },
    )
    assert r.status_code == 400 and "valeurs autorisées" in r.json()["error"]


# ------------------------------------------------------------- éditer du SQL


def test_editer_le_sql_d_un_modele(client: TestClient, project: Settings):
    uid = uid_of(client, "stg_customers")
    original = client.get(f"/api/dataset/{uid}").json()["raw_sql"]
    r = client.put(f"/api/dataset/{uid}/sql", json={"sql": original + "\n-- édité\n"})
    assert r.status_code == 200 and r.json()["parse_error"] is None
    assert "-- édité" in (project.project_dir / r.json()["path"]).read_text()
    client.put(f"/api/dataset/{uid}/sql", json={"sql": original})


def test_un_seed_n_est_pas_editable(client: TestClient):
    r = client.put(
        f"/api/dataset/{uid_of(client, 'raw_orders')}/sql", json={"sql": "select 1"}
    )
    assert r.status_code == 400 and "modèles" in r.json()["error"]


# ------------------------------------------------------------- bibliothèque


def test_le_catalogue_des_processeurs(client: TestClient):
    data = client.get("/api/processors").json()
    assert len(data["processors"]) >= 18
    assert "Colonnes" in data["categories"]
    assert {t["key"] for t in data["recipe_types"]} >= {
        "prepare",
        "join",
        "group",
        "stack",
        "sql",
    }
    assert data["materializations"] == ["view", "table", "incremental", "ephemeral"]
    assert data["strategies_needing_key"] == ["delete+insert", "merge"]


def test_les_references_disponibles(client: TestClient):
    refs = client.get("/api/refs").json()["refs"]
    by_label = {r["label"]: r for r in refs}
    assert by_label["stg_orders"]["kind"] == "ref"
    assert by_label["raw_orders"]["resource"] == "seed"


def test_les_couches_et_la_suggestion(client: TestClient):
    layers_list = {c["name"]: c for c in client.get("/api/layers").json()["layers"]}
    assert layers_list["marts"]["materialized"] == "table"

    out = client.post(
        "/api/layers/suggest", json={"inputs": [{"ref": "raw_orders"}]}
    ).json()
    assert out["layer"] == "staging" and out["reason"]
    out = client.post(
        "/api/layers/suggest", json={"inputs": [{"ref": "stg_orders"}]}
    ).json()
    assert out["layer"] == "intermediate"


# ------------------------------------------------------------------ recipes


RECIPE = {
    "name": "clients_fideles",
    "type": "prepare",
    "inputs": [{"ref": "customer_orders", "alias": "customer_orders"}],
    "output": {
        "layer": "marts",
        "materialized": "table",
        "description": "Clients avec au moins deux commandes",
    },
    "steps": [
        {
            "id": "a",
            "type": "filter_formula",
            "enabled": True,
            "params": {"condition": "nb_orders >= 2", "action": "keep"},
        },
        {
            "id": "b",
            "type": "round",
            "enabled": True,
            "params": {"column": "revenue_eur", "decimals": 0},
        },
        {
            "id": "c",
            "type": "rename",
            "enabled": True,
            "params": {"renames": [{"from": "full_name", "to": "client"}]},
        },
    ],
}


def test_les_colonnes_d_entree_viennent_de_l_entrepot(client: TestClient):
    cols = client.post("/api/recipe/columns", json={"spec": RECIPE}).json()["columns"]
    assert [c["name"] for c in cols["customer_orders"]] == [
        "customer_id",
        "full_name",
        "nb_orders",
        "revenue_eur",
    ]


def test_compiler_une_recipe_sans_rien_ecrire(client: TestClient, project: Settings):
    out = client.post("/api/recipe/compile", json={"spec": RECIPE}).json()
    assert out["path"] == "models/marts/clients_fideles.sql"
    assert out["exists"] is False
    assert "{{ ref('customer_orders') }}" in out["sql"]
    assert not (project.project_dir / out["path"]).exists()


def test_l_apercu_calcule_sans_construire(client: TestClient):
    out = client.post("/api/recipe/preview", json={"spec": RECIPE, "limit": 50}).json()
    columns = [c["name"] for c in out["columns"]]
    assert columns == ["customer_id", "client", "nb_orders", "revenue_eur"]
    assert sorted(r[columns.index("customer_id")] for r in out["rows"]) == [1, 3]
    assert out["deltas"][2]["created"] == ["client"]
    assert out["deltas"][2]["deleted"] == ["full_name"]
    assert out["suggestions"]["client"], "chaque colonne propose des étapes"


def test_l_apercu_s_arrete_a_l_etape_demandee(client: TestClient):
    out = client.post(
        "/api/recipe/preview", json={"spec": RECIPE, "upto": 1, "limit": 50}
    ).json()
    assert [c["name"] for c in out["columns"]] == [
        "customer_id",
        "full_name",
        "nb_orders",
        "revenue_eur",
    ]
    assert len(out["rows"]) == 2


def test_une_recipe_fausse_est_refusee_avant_l_ecriture(client: TestClient):
    casing = {
        **RECIPE,
        "steps": [
            {
                "id": "x",
                "type": "round",
                "enabled": True,
                "params": {"column": "colonne_absente"},
            }
        ],
    }
    r = client.post("/api/recipe/preview", json={"spec": casing})
    assert r.status_code == 400 and "colonne_absente" in r.json()["error"]


def test_enregistrer_une_recipe_ecrit_le_sql_et_le_script(
    client: TestClient, project: Settings
):
    out = client.post("/api/recipe/save", json={"spec": RECIPE, "run": False}).json()
    assert out["saved"] is True
    assert out["parse_error"] is None
    assert out["unique_id"] == "model.atelier_demo.clients_fideles"

    model = project.project_dir / out["path"]
    script = project.project_dir / out["recipe_path"]
    assert model.exists() and script.exists()
    assert "materialized='table'" in model.read_text()
    assert script.parts[-3:] == (".pliq", "recipes", "clients_fideles.yml")

    # La description passe dans le schema.yml voisin.
    schema = project.project_dir / "models" / "marts" / "schema.yml"
    assert "Clients avec au moins deux commandes" in schema.read_text()

    # Et le script est relisible tel quel.
    reread = client.get("/api/recipe/clients_fideles").json()["spec"]
    assert reread["steps"][0]["params"]["condition"] == "nb_orders >= 2"


def test_le_modele_enregistre_se_construit_vraiment(
    client: TestClient, project: Settings
):
    """La boucle complète : aperçu → enregistrement → dbt build → table en base."""
    client.post("/api/recipe/save", json={"spec": RECIPE, "run": False})

    r = client.post("/api/run", json={"command": "build", "select": "clients_fideles"})
    assert r.status_code == 200
    state = wait_for_run_end(client)
    assert state["run"]["success"] is True, [line["msg"] for line in state["lines"]]
    assert any(
        line.get("node", "").endswith("clients_fideles") for line in state["lines"]
    )

    uid = "model.atelier_demo.clients_fideles"
    assert state["states"][uid]["status"] == "success"

    # On relit par l'atelier lui-même : c'est le chemin de l'utilisateur, et
    # Pliq n'ouvre plus jamais l'entrepôt en direct.
    data = client.post(f"/api/dataset/{uid}/explore", json={"limit": 10}).json()
    order = [c["name"] for c in data["columns"]]
    wanted = ["customer_id", "client", "nb_orders", "revenue_eur"]
    lines = sorted(
        [tuple(r[order.index(c)] for c in wanted) for r in data["rows"]],
        key=lambda r: r[0],
    )
    assert lines == [
        (1, "Jean Dupont", 2, 121.0),  # round(120.50)
        (3, "Luis Garcia", 2, 10.0),
    ]


def test_le_script_visuel_d_un_modele_absent(client: TestClient):
    assert client.get("/api/recipe/stg_orders").status_code == 404


TO_DELETE = {
    **RECIPE,
    "name": "a_supprimer",
    "output": {**RECIPE["output"], "description": "Modèle de passage"},
}


def test_supprimer_une_recipe_efface_le_script_le_modele_et_sa_doc(
    client: TestClient, project: Settings
):
    out = client.post("/api/recipe/save", json={"spec": TO_DELETE}).json()
    model = project.project_dir / out["path"]
    script = project.project_dir / out["recipe_path"]
    schema = project.project_dir / "models" / "marts" / "schema.yml"
    assert model.exists() and script.exists()
    assert "Modèle de passage" in schema.read_text()

    r = client.post("/api/recipe/delete", json={"name": "a_supprimer"})
    assert r.status_code == 200, r.json()
    assert r.json()["parse_error"] is None
    assert not script.exists() and not model.exists()
    # La doc d'un modèle disparu partirait en avertissement à chaque parse.
    assert not schema.exists() or "a_supprimer" not in schema.read_text()
    assert "a_supprimer" not in [
        d["name"] for d in client.get("/api/flow?refresh=true").json()["datasets"]
    ]


def test_supprimer_le_script_seul_rend_le_modele_a_la_main(
    client: TestClient, project: Settings
):
    out = client.post("/api/recipe/save", json={"spec": TO_DELETE}).json()
    model = project.project_dir / out["path"]

    r = client.post(
        "/api/recipe/delete", json={"name": "a_supprimer", "delete_model": False}
    )
    assert r.status_code == 200, r.json()
    assert model.exists(), "le SQL reste : c'est tout l'intérêt de ce choix"
    assert not (project.project_dir / out["recipe_path"]).exists()
    assert client.get("/api/recipe/a_supprimer").status_code == 404

    # Le Flow le montre encore, mais comme un modèle écrit à la main.
    flow = client.get("/api/flow?refresh=true").json()
    recipe = next(r for r in flow["recipes"] if r["model_name"] == "a_supprimer")
    assert recipe["managed"] is False

    # On rend le projet à son état d'origine pour les tests suivants.
    client.post("/api/recipe/delete", json={"name": "a_supprimer"})


def test_supprimer_une_recipe_inconnue_le_dit(client: TestClient):
    r = client.post("/api/recipe/delete", json={"name": "jamais_vue"})
    assert r.status_code == 404


def test_un_nom_hostile_ne_supprime_rien_hors_du_projet(client: TestClient):
    r = client.post("/api/recipe/delete", json={"name": "../../dbt_project"})
    assert r.status_code == 400


# --------------------------------- séquences : ce qui se casse entre deux gestes
#
# Les tests ci-dessus vérifient chacun un geste. Ceux-ci vérifient un
# enchaînement : c'est là que les défauts restaient, parce qu'aucun geste pris
# isolément n'est fautif.


RECIPE_CONFLICT = {
    **RECIPE,
    "name": "conflit_script",
    "output": {**RECIPE["output"], "description": "Script à deux onglets"},
}


def test_une_modification_invisible_dans_le_sql_est_quand_meme_protegee(
    client: TestClient, project: Settings
):
    """Désactiver une étape ne change ni le SQL produit ni la documentation.

    Les empreintes ne couvraient que ces deux-là : les deux onglets étaient donc
    d'accord, et le second écrasait la recipe du premier avec un 200. La
    modification disparaissait sans que rien ne l'ait signalée — ni à l'écran,
    ni dans le fichier `.sql`, qui n'avait effectivement pas bougé.
    """
    creates = client.post(
        "/api/recipe/save", json={"spec": RECIPE_CONFLICT, "is_new": True}
    )
    assert creates.status_code == 200, creates.json()
    out = creates.json()
    model = project.project_dir / out["path"]
    try:
        # L'onglet A ouvre la recipe et garde ce qu'il a lu.
        read_value = client.get("/api/recipe/conflit_script").json()
        spec_a, base_a = read_value["spec"], read_value["base"]
        sql_before = model.read_text()

        # L'onglet B ajoute une étape désactivée, et enregistre.
        spec_b = json.loads(json.dumps(spec_a))
        spec_b["steps"].append(
            {
                "id": "zz",
                "type": "filter_formula",
                "enabled": False,
                "params": {"condition": "nb_orders >= 99", "action": "keep"},
            }
        )
        rb = client.post("/api/recipe/save", json={"spec": spec_b, "base": base_a})
        assert rb.status_code == 200, rb.json()
        assert model.read_text() == sql_before, (
            "prémisse du test : une étape désactivée ne change pas le SQL — "
            "c'est précisément pourquoi l'empreinte du SQL ne suffisait pas"
        )

        # L'onglet A, resté sur la version d'avant, réenregistre.
        ra = client.post("/api/recipe/save", json={"spec": spec_a, "base": base_a})
        assert ra.status_code == 409, ra.json()
        assert "script visuel" in ra.json()["error"]

        after = client.get("/api/recipe/conflit_script").json()["spec"]
        assert [s["id"] for s in after["steps"]][
            -1
        ] == "zz", "l'étape désactivée de l'onglet B doit être encore là"
    finally:
        client.post(
            "/api/recipe/delete",
            json={"name": "conflit_script", "delete_model": True},
        )


FAILED_DELETION = {
    **RECIPE,
    "name": "suppression_ratee",
    "output": {**RECIPE["output"], "description": "Modèle à supprimer"},
}


def test_une_suppression_qui_echoue_ne_laisse_rien_a_moitie_efface(
    client: TestClient, project: Settings
):
    """La route effaçait la recipe, puis le SQL, puis la doc — sans retour.

    Un `schema.yml` illisible faisait échouer le dernier geste : la réponse
    partait en 500, et le projet restait sans script ni modèle, avec une
    documentation orpheline. Rien ne permettait de revenir en arrière.
    """
    creates = client.post(
        "/api/recipe/save", json={"spec": FAILED_DELETION, "is_new": True}
    )
    assert creates.status_code == 200, creates.json()
    out = creates.json()
    model = project.project_dir / out["path"]
    script = project.project_dir / out["recipe_path"]
    schema = project.project_dir / "models" / "marts" / "schema.yml"
    before_text = schema.read_text()
    assert model.exists() and script.exists()

    try:
        schema.write_text(before_text + "\ncasse: [\n")
        r = client.post(
            "/api/recipe/delete",
            json={"name": "suppression_ratee", "delete_model": True},
        )
        assert r.status_code == 400, r.json()
        assert "schema.yml" in r.json()["error"], "il faut dire quel fichier réparer"
        assert script.exists(), "le script visuel ne doit pas avoir disparu"
        assert model.exists(), "le modèle ne doit pas avoir disparu"
    finally:
        schema.write_text(before_text)
        client.post(
            "/api/recipe/delete",
            json={"name": "suppression_ratee", "delete_model": True},
        )


# ---------------------------------------------------------- nouveaux datasets


def test_l_inventaire_de_l_entrepot_marque_ce_que_dbt_connait(client: TestClient):
    data = client.get("/api/warehouse/tables").json()
    by_name = {t["name"]: t for t in data["tables"]}
    assert by_name["raw_orders"]["declared"]["kind"] == "seed"
    assert by_name["customer_orders"]["declared"]["kind"] == "model"


def test_declarer_une_table_de_l_entrepot_en_source(
    client: TestClient, project: Settings
):
    # Une table que dbt ne gère pas : on la pose nous-mêmes, donc il faut
    # d'abord rendre la base à ce test (voir release_duckdb_for_test).
    release_duckdb_for_test()
    con = duckdb.connect(str(project.database_path()))
    try:
        con.execute("create table if not exists main.legacy_contacts as select 1 as id")
    finally:
        con.close()

    out = client.post(
        "/api/datasets/source",
        json={
            "source_name": "legacy",
            "schema_name": "main",
            "tables": [{"name": "legacy_contacts", "description": "Import historique"}],
        },
    ).json()
    assert out["added"] == ["legacy_contacts"]
    assert out.get("parse_error") is None

    refs = client.get("/api/refs").json()["refs"]
    assert any(r["label"] == "legacy.legacy_contacts" for r in refs)


# --------------------------------------------------------------------- runs


def test_une_commande_non_autorisee_est_refusee(client: TestClient):
    r = client.post("/api/run", json={"command": "clean"})
    assert r.status_code == 400 and "non autorisée" in r.json()["error"]


def test_lancer_les_tests_dbt_depuis_l_atelier(client: TestClient):
    r = client.post("/api/run", json={"command": "test", "select": "stg_orders"})
    assert r.status_code == 200
    state = wait_for_run_end(client)
    assert state["run"]["success"] is True
    assert state["run"]["command"] == "test"
    assert state["run"]["duration"] is not None


def test_le_journal_du_run_est_diffuse_sur_le_websocket(client: TestClient):
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert "run" in hello and "states" in hello

        client.post("/api/run", json={"command": "compile", "select": "stg_orders"})
        seen = set()
        for _ in range(200):
            msg = ws.receive_json()
            seen.add(msg["type"])
            if msg["type"] == "run_done":
                break
        assert {"run_start", "run_done"} <= seen
    wait_for_run_end(client)


# ------------------------------------------------------------------ statique


def test_l_interface_est_servie(client: TestClient):
    page = client.get("/")
    assert page.status_code == 200 and "<html" in page.text.lower()
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_la_documentation_est_servie(client: TestClient):
    """Le `?` du bandeau mène ici : la page, ses feuilles et ses pages `.md`."""
    page = client.get("/doc")
    assert page.status_code == 200 and "<html" in page.text.lower()
    assert client.get("/static/doc/doc.js").status_code == 200
    assert client.get("/static/doc/sommaire.json").status_code == 200
    home = client.get("/static/doc/pages/index.md")
    assert home.status_code == 200 and home.text.startswith("# ")


# =========================================================================
# Régressions d'audit : chaque test ci-dessous monte son propre projet, car
# il touche au projet actif (chemins, parse cassé, changement de projet).
# =========================================================================


def _fresh_project(parent: Path, name: str, model_paths: str | None = None) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    root = projects.create(parent, name)
    if model_paths:
        yml = (root / "dbt_project.yml").read_text()
        assert 'model-paths: ["models"]' in yml
        (root / "dbt_project.yml").write_text(
            yml.replace('model-paths: ["models"]', f'model-paths: ["{model_paths}"]')
        )
    return root


@pytest.fixture
def nested_workshop(tmp_path: Path):
    """Un atelier sur un projet dont les modèles vivent dans transform/models."""
    root = _fresh_project(tmp_path / "a", "atelier_a", "transform/models")
    os.chdir(root)
    with TestClient(create_app(load_settings(project_dir=str(root)))) as c:
        yield c, root


PACKAGE_YML = """\
name: 'vendor'
version: '1.0.0'
config-version: 2
model-paths: ["models"]
"""


@pytest.fixture
def workshop_with_package(tmp_path: Path):
    """Un projet qui installe un paquet local, lequel a un `orders` lui aussi.

    Le cas exact du piège : deux `models/orders.sql`, un dans chaque projet.
    dbt les distingue par leur paquet ; l'atelier ne résolvait les chemins que
    depuis le projet ouvert.
    """
    package = tmp_path / "vendor"
    write(package / "dbt_project.yml", PACKAGE_YML)
    # Même nom de nœud et même chemin de fichier que le modèle local — c'est le
    # piège. L'alias, lui, doit différer : dbt refuse d'écrire deux modèles
    # dans la même table, et ce n'est pas ce qu'on teste ici.
    write(
        package / "models" / "orders.sql",
        "{{ config(alias='orders_vendor') }}\nselect 1 as vendu_par_le_paquet\n",
    )

    root = _fresh_project(tmp_path / "p", "atelier_p")
    write(root / "packages.yml", f"packages:\n  - local: {package}\n")
    write(root / "models" / "orders.sql", "select 2 as ecrit_par_moi\n")

    os.chdir(root)
    settings = load_settings(project_dir=str(root))
    svc = DbtService(settings)
    assert svc.invoke(["deps"], reset=True).success, "dbt deps doit passer"
    # Construit : `dbt show --inline` ne lit que ce qui existe dans l'entrepôt,
    # et c'est bien la *valeur* lue qui dit lequel des deux `orders` a servi.
    assert svc.invoke(["build"], reset=True).success, "dbt build doit passer"
    with TestClient(create_app(settings)) as c:
        yield c, root


def _uid_at(client: TestClient, name: str, package: str) -> str:
    """L'uid d'un dataset, désigné par son paquet : deux peuvent porter le même
    nom, et c'est justement le cas qu'on teste."""
    flow = client.get("/api/flow?refresh=true").json()
    for d in flow["datasets"]:
        if d["name"] == name and d.get("package") == package:
            return d["id"]
    raise AssertionError(f"{name} du paquet {package} absent du Flow")


def test_un_modele_de_paquet_se_lit_dans_son_paquet(workshop_with_package):
    """Ouvrir `model.vendor.orders` montrait le `orders.sql` du projet ouvert.

    Les chemins étaient résolus depuis le projet actif sans regarder à qui le
    nœud appartient : deux fichiers du même nom, et c'est le local qu'on lisait.
    """
    client, _root = workshop_with_package
    d = client.get(f"/api/dataset/{_uid_at(client, 'orders', 'vendor')}").json()

    assert d["package"] == "vendor"
    assert d["editable"] is False
    assert "vendu_par_le_paquet" in d["raw_sql"], "c'est le SQL du paquet qu'on lit"
    assert "ecrit_par_moi" not in d["raw_sql"]
    # Rien à rendre : il n'y a pas de fichier de ce projet à se prouver à jour.
    assert d["sql_digest"] is None


def test_enregistrer_un_modele_de_paquet_n_ecrase_pas_le_modele_local(
    workshop_with_package,
):
    """Le cas de perte de travail : 200, et le modèle local remplacé.

    L'atelier écrivait `models/orders.sql` du projet ouvert en croyant modifier
    celui du paquet. Les trois routes qui écrivent passent maintenant par la
    même porte.
    """
    client, root = workshop_with_package
    uid = _uid_at(client, "orders", "vendor")
    local = root / "models" / "orders.sql"

    r = client.put(f"/api/dataset/{uid}/sql", json={"sql": "select 3 as ecrase"})
    assert r.status_code == 403, r.json()
    assert "vendor" in r.json()["error"]
    assert local.read_text() == "select 2 as ecrit_par_moi\n", "intact"

    # La fiche et les tests écrivent un YAML : même porte, même refus.
    doc = client.post(
        f"/api/dataset/{uid}/doc",
        json={"description": "la mienne", "columns": []},
    )
    assert doc.status_code == 403, doc.json()
    tests = client.post(
        f"/api/dataset/{uid}/tests",
        json={"description": "", "columns": [], "tags": []},
    )
    assert tests.status_code == 403, tests.json()
    assert not (root / "models" / "schema.yml").exists(), "aucun YAML écrit ici"


def test_le_modele_du_projet_reste_editable_malgre_le_paquet(workshop_with_package):
    """La porte ne doit pas se refermer sur le projet lui-même."""
    client, root = workshop_with_package
    uid = _uid_at(client, "orders", "atelier_p")  # même nom, autre paquet
    assert uid == "model.atelier_p.orders"

    d = client.get(f"/api/dataset/{uid}").json()
    assert d["editable"] is True and "ecrit_par_moi" in d["raw_sql"]
    r = client.put(
        f"/api/dataset/{uid}/sql",
        json={"sql": "select 4 as toujours_a_moi", "base": d["sql_digest"]},
    )
    assert r.status_code == 200, r.json()
    assert "toujours_a_moi" in (root / "models" / "orders.sql").read_text()


def test_une_recipe_lit_le_modele_de_paquet_qu_on_a_choisi(workshop_with_package):
    """La sélection précise disparaissait entre l'inventaire et la compilation.

    `/api/refs` distingue les deux `orders` par leur `id`, mais l'entrée de
    recipe ne gardait que le nom : `ref('orders')` nu, que dbt résout dans le
    projet ouvert. L'atelier montrait le modèle du paquet et compilait celui du
    projet — 200, aucune erreur, l'autre dataset.
    """
    client, _ = workshop_with_package
    refs = {r["id"]: r for r in client.get("/api/refs").json()["refs"]}
    chosen = refs["model.vendor.orders"]

    # Le libellé doit déjà les séparer : deux « orders » identiques dans la
    # liste, et personne ne peut désigner le bon.
    assert chosen["label"] != refs["model.atelier_p.orders"]["label"]
    assert chosen["package"] == "vendor"
    assert refs["model.atelier_p.orders"]["package"] == ""

    spec = {
        "name": "depuis_le_paquet",
        "type": "prepare",
        "steps": [],
        # Ce que `toSpecInput` construit côté navigateur.
        "inputs": [
            {"ref": chosen["ref"], "alias": chosen["ref"], "package": chosen["package"]}
        ],
    }
    r = client.post("/api/recipe/preview", json={"spec": spec})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert [c["name"] for c in payload["columns"]] == ["vendu_par_le_paquet"]
    assert payload["rows"] == [[1]], "le dataset du paquet, pas son homonyme"

    sql = client.post("/api/recipe/compile", json={"spec": spec}).json()["sql"]
    assert "ref('vendor', 'orders')" in sql


def test_sans_paquet_une_entree_reste_celle_du_projet(workshop_with_package):
    """Le contre-exemple, et la compatibilité : les recipes déjà écrites ne
    nomment aucun paquet, et doivent continuer de lire le modèle local."""
    client, _ = workshop_with_package
    spec = {
        "name": "depuis_le_projet",
        "type": "prepare",
        "steps": [],
        "inputs": [{"ref": "orders", "alias": "orders"}],
    }
    r = client.post("/api/recipe/preview", json={"spec": spec})
    assert r.status_code == 200, r.text
    assert [c["name"] for c in r.json()["columns"]] == ["ecrit_par_moi"]


VERSIONED_SCHEMA = """\
version: 2

models:
  - name: customers
    description: commune aux deux versions
    latest_version: 2
    columns:
      - name: id
        description: identifiant, commun
    versions:
      - v: 1
        description: version une
        columns:
          - name: id
            description: identifiant de la v1
      - v: 2
        description: version deux
"""


@pytest.fixture
def versioned_workshop(tmp_path: Path):
    """Un projet dont `customers` existe en deux versions, avec un consommateur.

    dbt donne le même `name` aux deux nœuds — `customers` — et les distingue
    par leur `version`. Tout ce qui documente ne recevait que le nom.
    """
    root = _fresh_project(tmp_path / "v", "atelier_v")
    write(root / "models" / "customers_v1.sql", "select 1 as id\n")
    write(root / "models" / "customers_v2.sql", "select 2 as id\n")
    # Chaque version porte sa propre description, par-dessus la commune : c'est
    # cet héritage que la documentation de l'atelier ne traitait pas.
    write(root / "models" / "versions.yml", VERSIONED_SCHEMA)
    write(
        root / "models" / "lecteur.sql", "select * from {{ ref('customers', v=2) }}\n"
    )
    os.chdir(root)
    settings = load_settings(project_dir=str(root))
    assert DbtService(settings).invoke(["build"], reset=True).success
    with TestClient(create_app(settings)) as c:
        yield c, root


def test_renommer_un_modele_versionne_est_refuse(versioned_workshop):
    """Un seul fichier déplacé, et la dernière version change de logique.

    `_model_node` rendait la première version rencontrée. Le renommage
    déplaçait ce seul fichier vers `clients.sql` — donc sans suffixe, donc
    *dernière* version du nouveau nom —, renommait l'entrée YAML entière et
    réécrivait les `ref()` de l'aval, versions comprises. Le consommateur de
    `v=2` se mettait à lire la v1 : `renamed: true`, `parse_error: null`, et un
    `dbt build` qui passe.
    """
    client, root = versioned_workshop

    preview = client.post(
        "/api/recipe/rename",
        json={"name": "customers", "new_name": "clients", "dry_run": True},
    )
    assert preview.status_code == 409, preview.text
    assert "versionné" in preview.json()["error"]

    r = client.post(
        "/api/recipe/rename", json={"name": "customers", "new_name": "clients"}
    )
    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert "v1" in error and "v2" in error

    # Rien n'a bougé : ni les fichiers, ni ce que lit le consommateur.
    remaining = sorted(p.name for p in (root / "models").glob("*.sql"))
    assert remaining == ["customers_v1.sql", "customers_v2.sql", "lecteur.sql"]
    assert not (root / "models" / "clients.sql").exists()


def test_supprimer_un_modele_versionne_est_refuse(versioned_workshop):
    """Même identité manquante, même demi-traitement : un fichier sur deux."""
    client, root = versioned_workshop
    r = client.post(
        "/api/recipe/delete", json={"name": "customers", "delete_model": True}
    )
    assert r.status_code == 409, r.text
    assert "versionné" in r.json()["error"]
    assert (root / "models" / "customers_v1.sql").exists()
    assert (root / "models" / "customers_v2.sql").exists()


def test_une_recipe_va_sous_les_model_paths_imbriques(nested_workshop):
    """dbt ne découvre que ce qui est sous ses `model-paths`.

    Ne garder que le dernier dossier écrivait dans `models/` : l'API annonçait
    une sauvegarde réussie, et dbt ne voyait jamais le modèle.
    """
    client, root = nested_workshop
    response = client.post(
        "/api/recipe/save",
        json={
            "spec": {"name": "ventes", "type": "sql", "sql": "select 1 as x"},
            "run": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "transform/models/ventes.sql"
    assert body["parse_error"] is None
    assert (root / "transform" / "models" / "ventes.sql").exists()
    assert not (root / "models" / "ventes.sql").exists()
    # dbt le découvre pour de bon : il a un unique_id dans le manifest.
    assert body["unique_id"] == "model.atelier_a.ventes"


def test_une_reference_inexistante_remonte_a_la_sauvegarde(nested_workshop):
    """`parse()` rend un manifest périmé quand il en a un : il faut le dire.

    Sans ça la route répondait `saved: true, parse_error: null`, et pouvait
    enchaîner sur un build alors que le projet ne parse plus.
    """
    client, _ = nested_workshop
    client.post(
        "/api/recipe/save",
        json={
            "spec": {"name": "ventes", "type": "sql", "sql": "select 1 as x"},
            "run": False,
        },
    )  # un premier parse réussi : c'est lui qui masquait l'échec suivant
    body = client.post(
        "/api/recipe/save",
        json={
            "spec": {
                "name": "casse",
                "type": "sql",
                "sql": "select * from {{ ref('nexiste_pas') }}",
            },
            "run": False,
        },
    ).json()
    assert body["saved"] is True
    assert body["parse_error"] and "nexiste_pas" in body["parse_error"]
    assert body["unique_id"] is None


def test_ouvrir_un_projet_pendant_une_sauvegarde_n_ecrit_pas_dans_l_autre(
    nested_workshop, tmp_path: Path, monkeypatch
):
    """Le changement de projet doit attendre la fin des écritures en cours.

    `settings`, `svc` et `wh` sont des cellules partagées : une sauvegarde qui
    lit ses colonnes dans A puis écrit avec les cellules réaffectées atterrit
    dans B, avec un HTTP 200 et un fichier introuvable côté A.
    """
    client, a = nested_workshop
    b = _fresh_project(tmp_path / "b", "atelier_b")
    client.post(
        "/api/recipe/save",
        json={
            "spec": {"name": "ventes", "type": "sql", "sql": "select 1 as x"},
            "run": False,
        },
    )

    input_entry, resume_point = threading.Event(), threading.Event()

    def slow_columns(self, sql: str, limit: int = 1):
        """Fige la sauvegarde pile entre la lecture des colonnes et l'écriture."""
        input_entry.set()
        assert resume_point.wait(30), "le test n'a jamais relâché la sauvegarde"
        return [{"name": "x", "type": "INTEGER"}]

    monkeypatch.setattr(Warehouse, "columns_of_sql", slow_columns)

    backup: dict = {}
    opening: dict = {}

    def save_it():
        backup["r"] = client.post(
            "/api/recipe/save",
            json={
                "spec": {
                    "name": "concurrent",
                    "type": "prepare",
                    "inputs": [{"ref": "ventes", "alias": "ventes"}],
                    "steps": [],
                },
                "run": False,
            },
        ).json()

    def open_it():
        opening["r"] = client.post("/api/projects/open", json={"path": str(b)}).json()

    save_task = threading.Thread(target=save_it)
    save_task.start()
    assert input_entry.wait(
        30
    ), "la sauvegarde n'a jamais atteint la lecture des colonnes"

    open_task = threading.Thread(target=open_it)
    open_task.start()
    time.sleep(1.0)
    assert "r" not in opening, "le projet a changé pendant une sauvegarde"

    resume_point.set()
    save_task.join(60)
    open_task.join(60)

    assert backup["r"]["saved"] is True
    assert opening["r"]["opened"] == str(b)
    assert (a / "transform" / "models" / "concurrent.sql").exists()
    assert not (b / "models" / "concurrent.sql").exists()


def test_ouvrir_un_projet_pendant_un_lancement_de_run_est_refuse(
    nested_workshop, tmp_path: Path, monkeypatch
):
    """`/api/run` était la seule route d'écriture hors du verrou de bascule.

    Entre la lecture de la cellule `svc` et le départ du thread dbt, une
    bascule pouvait passer : elle faisait `os.chdir` vers l'autre projet, et le
    build partait écrire dans le `dev.duckdb` du voisin.
    """
    client, a = nested_workshop
    b = _fresh_project(tmp_path / "b", "atelier_b")
    client.post(
        "/api/recipe/save",
        json={
            "spec": {"name": "ventes", "type": "sql", "sql": "select 1 as x"},
            "run": False,
        },
    )

    input_entry, resume_point = threading.Event(), threading.Event()
    real_start_run = DbtService.start_run

    def start_run_lent(self, *args, **kwargs):
        """Fige le lancement pile là où la bascule se glissait."""
        input_entry.set()
        assert resume_point.wait(30), "le test n'a jamais relâché le lancement"
        return real_start_run(self, *args, **kwargs)

    monkeypatch.setattr(DbtService, "start_run", start_run_lent)

    launch: dict = {}
    opening: dict = {}

    def launch_it():
        launch["r"] = client.post(
            "/api/run", json={"command": "build", "select": "ventes"}
        )

    def open_it():
        opening["r"] = client.post("/api/projects/open", json={"path": str(b)})

    run_task = threading.Thread(target=launch_it)
    run_task.start()
    assert input_entry.wait(30), "le lancement n'a jamais atteint start_run"

    open_task = threading.Thread(target=open_it)
    open_task.start()
    time.sleep(1.0)
    assert "r" not in opening, "le projet a changé pendant un lancement de run"

    resume_point.set()
    run_task.join(60)
    open_task.join(60)

    assert launch["r"].status_code == 200
    # Une fois le lancement passé, la bascule voit le run et le dit clairement.
    assert opening["r"].status_code == 409
    assert "en cours" in opening["r"].json()["error"]

    wait_for_run_end(client)
    assert os.path.realpath(os.getcwd()) == os.path.realpath(a)
    assert not (b / "dev.duckdb").exists(), "le build a écrit dans l'autre projet"


def test_une_lecture_pendant_un_build_est_refusee_au_lieu_d_attendre(
    client: TestClient, project: Settings
):
    """dbt n'accepte qu'une invocation à la fois, et c'était une attente muette.

    `invoke` tient son verrou pour toute la durée d'un `dbt build`. Une lecture
    partie pendant ce temps ne se trompait pas : elle attendait, parfois des
    minutes — et elle attendait en tenant le verrou de projet du serveur, donc
    toutes les autres routes faisaient la queue derrière elle. Quelques clics
    pendant un build, et l'atelier ne répondait plus à rien.

    On refuse maintenant tout de suite, avec de quoi comprendre. Ce qui ne
    passe pas par dbt — l'état du run, la fiche du projet — continue de
    répondre : c'est justement ce qu'on regarde pendant un build.
    """
    uid = uid_of(client, "stg_orders")
    client.post("/api/run", json={"command": "build", "select": "stg_orders"})

    refusal = []
    end = time.time() + 60
    while time.time() < end:
        if not client.get("/api/run").json()["run"]["running"]:
            break
        r = client.post(f"/api/dataset/{uid}/explore", json={"limit": 5})
        if r.status_code == 409:
            refusal.append(r)
            break

    wait_for_run_end(client)
    assert refusal, "la lecture a attendu la fin du build au lieu de le dire"
    body = refusal[0].json()
    assert body["code"] == "run_in_progress"
    assert "en cours" in body["error"]

    # Et la lecture remarche dès que dbt est libre.
    reread_pass = client.post(f"/api/dataset/{uid}/explore", json={"limit": 5})
    assert reread_pass.status_code == 200


def test_une_ecriture_pendant_un_build_est_refusee_avant_de_toucher_au_disque(
    client: TestClient, project: Settings
):
    """dbt lit les fichiers du projet pendant qu'il construit.

    Réécrire un `.sql` au milieu d'un build donne un modèle construit à partir
    d'un fichier qui n'existe plus, et rien ne le dit. Le renommage posait déjà
    cette règle pour lui seul ; elle vaut pour tout ce qui écrit.
    """
    uid = uid_of(client, "stg_orders")
    before = (project.project_dir / "models" / "staging" / "stg_orders.sql").read_text()

    client.post("/api/run", json={"command": "build", "select": "stg_orders"})

    refusal = []
    end = time.time() + 60
    while time.time() < end:
        if not client.get("/api/run").json()["run"]["running"]:
            break
        r = client.put(f"/api/dataset/{uid}/sql", json={"sql": "select 1 as x"})
        if r.status_code == 409:
            refusal.append(r)
            break

    wait_for_run_end(client)
    assert refusal, "l'écriture est passée pendant un build"
    assert "en cours" in refusal[0].json()["error"]
    after = (project.project_dir / "models" / "staging" / "stg_orders.sql").read_text()
    assert after == before, "le fichier a été touché malgré le refus"


def test_l_etat_du_run_reste_lisible_pendant_un_build(client: TestClient):
    """Le refus ne doit pas s'étendre à ce qui ne passe pas par dbt.

    C'est précisément ce qu'on regarde pendant un build : le journal, l'état
    des nœuds, le projet ouvert. Ces routes ne prennent pas le verrou de
    projet et n'invoquent jamais dbt — elles doivent répondre.
    """
    client.post("/api/run", json={"command": "build", "select": "stg_orders"})
    try:
        assert client.get("/api/run").status_code == 200
        assert client.get("/api/project").status_code == 200
        assert client.get("/api/projects").status_code == 200
    finally:
        wait_for_run_end(client)


def test_retirer_la_derniere_doc_d_une_recipe_la_retire_du_yaml(
    client: TestClient, project: Settings
):
    """Vidé de sa doc, le modèle sautait l'écriture : dbt gardait ses tests.

    L'API répondait « enregistré » et l'interface n'affichait plus rien, mais
    le `schema.yml` gardait le test — qui continuait d'échouer au build.
    """
    spec = {
        "name": "doc_effacee",
        "type": "prepare",
        "inputs": [{"ref": "customer_orders", "alias": "customer_orders"}],
        "steps": [],
        "output": {
            "layer": "marts",
            "materialized": "view",
            "description": "À effacer",
            "columns": [
                {
                    "name": "customer_id",
                    "description": "Clé du client",
                    "tests": [{"name": "unique", "severity": "error"}],
                }
            ],
        },
    }
    assert client.post("/api/recipe/save", json={"spec": spec}).json()["saved"]

    schema = project.project_dir / "models" / "marts" / "schema.yml"

    def node_card() -> dict:
        input_entries = yaml.safe_load(schema.read_text())["models"]
        return next((m for m in input_entries if m["name"] == "doc_effacee"), {})

    column = node_card()["columns"][0]
    assert column.get("data_tests") or column.get("tests") == ["unique"]

    # L'utilisateur retire la description et le test, puis réenregistre.
    empty = {
        **spec,
        "output": {
            **spec["output"],
            "description": "",
            "columns": [{"name": "customer_id", "description": "", "tests": []}],
        },
    }
    assert client.post("/api/recipe/save", json={"spec": empty}).json()["saved"]

    assert node_card() == {"name": "doc_effacee"}, "le test doit avoir disparu du YAML"

    # Et dbt ne le voit plus non plus.
    uid = uid_of(client, "doc_effacee")
    flow = client.get("/api/flow?refresh=true").json()
    target = next(d for d in flow["datasets"] if d["id"] == uid)
    assert target["tests"] == []


# ------------------------------------------- sauvegarde : collisions et conflits


@pytest.fixture
def fresh_workshop(tmp_path: Path):
    """Un atelier sur un projet vierge, à lui tout seul."""
    root = _fresh_project(tmp_path / "n", "atelier_n")
    os.chdir(root)
    with TestClient(create_app(load_settings(project_dir=str(root)))) as c:
        yield c, root


def _sql_recipe(name: str = "ventes", sql: str = "select 1 as x") -> dict:
    return {"name": name, "type": "sql", "sql": sql}


def test_une_nouvelle_recipe_n_ecrase_pas_un_modele_existant(fresh_workshop):
    """Nommer une recipe comme un modèle déjà écrit remplaçait sa logique.

    La route calculait le chemin puis écrivait : elle ne distinguait pas une
    création d'une mise à jour, et répondait 200 en ayant effacé le travail
    de quelqu'un.
    """
    client, root = fresh_workshop
    domain = root / "models" / "ventes.sql"
    domain.parent.mkdir(parents=True, exist_ok=True)
    domain.write_text("-- logique métier écrite à la main\nselect 42 as reponse\n")

    r = client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})
    assert r.status_code == 409, r.json()
    assert "existe déjà" in r.json()["error"]
    assert "logique métier" in domain.read_text(), "le fichier ne doit pas bouger"
    assert not (root / ".pliq" / "recipes" / "ventes.yml").exists()


def test_une_recipe_neuve_passe_quand_la_place_est_libre(fresh_workshop):
    client, root = fresh_workshop
    r = client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})
    assert r.status_code == 200, r.json()
    assert (root / "models" / "ventes.sql").exists()
    # Et la sauvegarde suivante repart de l'empreinte rendue : pas de conflit.
    base = r.json()["base"]
    assert base["sql"]
    sequence = client.post(
        "/api/recipe/save",
        json={"spec": _sql_recipe(sql="select 2 as x"), "base": base},
    )
    assert sequence.status_code == 200, sequence.json()


def test_une_nouvelle_recipe_n_ecrase_pas_une_recipe_du_meme_nom(fresh_workshop):
    """Le .sql peut avoir été supprimé à la main : le script visuel, lui, est là."""
    client, root = fresh_workshop
    client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})
    (root / "models" / "ventes.sql").unlink()

    r = client.post(
        "/api/recipe/save",
        json={"spec": _sql_recipe(sql="select 5 as autre"), "is_new": True},
    )
    assert r.status_code == 409 and "existe déjà" in r.json()["error"]
    assert "select 1 as x" in (root / ".pliq" / "recipes" / "ventes.yml").read_text()


def test_une_modification_n_ecrase_pas_ce_qui_a_change_entre_temps(fresh_workshop):
    """L'éditeur doit retrouver la version qu'il a lue, ou renoncer."""
    client, root = fresh_workshop
    client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})
    read_value = client.get("/api/recipe/ventes").json()

    # Quelqu'un d'autre édite le .sql à la main pendant ce temps.
    model = root / "models" / "ventes.sql"
    model.write_text("-- retouché à la main\nselect 99 as x\n")

    r = client.post(
        "/api/recipe/save",
        json={"spec": _sql_recipe(sql="select 3 as x"), "base": read_value["base"]},
    )
    assert r.status_code == 409, r.json()
    assert "a changé" in r.json()["error"]
    assert "retouché à la main" in model.read_text()

    # Rouvrir la recipe donne la nouvelle empreinte — mais elle ne suffit plus :
    # le fichier ne dit toujours pas ce que le script visuel produit, et le
    # régénérer effacerait la modification. C'est un choix, pas un conflit.
    reread = client.get("/api/recipe/ventes").json()
    still_there = client.post(
        "/api/recipe/save",
        json={"spec": _sql_recipe(sql="select 3 as x"), "base": reread["base"]},
    )
    assert still_there.status_code == 409, still_there.json()
    assert still_there.json()["code"] == "sql_diverge"
    assert "retouché à la main" in model.read_text()

    # Et quand le choix est fait, l'écriture a lieu.
    ok = client.post(
        "/api/recipe/save",
        json={
            "spec": _sql_recipe(sql="select 3 as x"),
            "base": reread["base"],
            "overwrite_sql": True,
        },
    )
    assert ok.status_code == 200, ok.json()
    assert "select 3 as x" in model.read_text()


def test_le_sql_d_un_modele_de_recipe_ne_s_edite_pas_a_la_main(fresh_workshop):
    """L'atelier fabriquait lui-même la divergence dont il se plaint ensuite.

    Le fichier d'un modèle produit par un script visuel est regénéré à chaque
    enregistrement de la recipe : ce qu'on tape ici partirait avec. L'écran le
    sait — il n'offre alors que « Voir le SQL » — mais la route l'acceptait.
    """
    client, root = fresh_workshop
    out = client.post(
        "/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True}
    ).json()
    model = root / "models" / "ventes.sql"

    r = client.put(
        f"/api/dataset/{out['unique_id']}/sql", json={"sql": "select 99 as a_la_main"}
    )
    assert r.status_code == 409, r.json()
    assert "script visuel" in r.json()["error"]
    assert "select 1 as x" in model.read_text(), "intact"


def test_une_sauvegarde_perimee_ne_ressuscite_pas_un_modele_supprime(fresh_workshop):
    """Le contrôle d'empreinte sautait quand le fichier n'existait plus.

    Un `actuel is not None` de trop : la disparition du modèle ne comptait pas
    comme un changement, et un éditeur resté ouvert annulait silencieusement une
    suppression — 200, et le fichier de retour sur le disque. C'est pourtant le
    cas que `reject_if_stale` traite déjà pour les fiches et le SQL à la main.
    """
    client, root = fresh_workshop
    client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})
    read_value = client.get("/api/recipe/ventes").json()

    model = root / "models" / "ventes.sql"
    model.unlink()  # quelqu'un supprime le modèle pendant ce temps

    r = client.post(
        "/api/recipe/save",
        json={"spec": _sql_recipe(sql="select 3 as x"), "base": read_value["base"]},
    )
    assert r.status_code == 409, r.json()
    assert "supprimé" in r.json()["error"]
    assert not model.exists(), "un refus ne doit rien recréer"


def test_une_sauvegarde_refusee_ne_laisse_rien_derriere_elle(fresh_workshop):
    """Le SQL et la recipe étaient écrits avant la validation des tests.

    Une erreur tardive laissait les fichiers dans des versions différentes :
    ni l'état d'avant, ni celui d'après, et rien pour le dire.
    """
    client, root = fresh_workshop
    client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})
    model = root / "models" / "ventes.sql"
    recipe = root / ".pliq" / "recipes" / "ventes.yml"
    before_sql, before_recipe = model.read_text(), recipe.read_text()

    casing = {
        **_sql_recipe(sql="select 7 as x"),
        "output": {
            "columns": [
                {"name": "x", "tests": [{"name": "accepted_values", "values": []}]}
            ]
        },
    }
    r = client.post("/api/recipe/save", json={"spec": casing})
    assert r.status_code == 400 and "valeurs autorisées" in r.json()["error"]
    assert model.read_text() == before_sql
    assert recipe.read_text() == before_recipe
    assert not (root / "models" / "schema.yml").exists()


def test_reenregistrer_une_recipe_ne_defait_pas_la_fiche_dataset(fresh_workshop):
    """Deux écrans écrivaient la même chose à deux endroits différents.

    La fiche dataset écrit dans le YAML dbt ; l'éditeur de recipe rechargeait
    sa propre copie et la réappliquait. Réenregistrer une recipe sans toucher
    à ses tests annulait donc ce que la fiche venait d'écrire.
    """
    client, root = fresh_workshop
    spec = {
        **_sql_recipe(),
        "output": {
            "description": "ancienne",
            "columns": [{"name": "x", "tests": [{"name": "unique"}]}],
        },
    }
    client.post("/api/recipe/save", json={"spec": spec, "is_new": True})

    uid = "model.atelier_n.ventes"
    r = client.post(
        f"/api/dataset/{uid}/tests",
        json={
            "description": "nouvelle",
            "columns": [{"name": "x", "tests": [{"name": "not_null"}]}],
        },
    )
    assert r.status_code == 200, r.json()

    # L'éditeur relit : c'est le YAML dbt qui fait foi, pas la recipe.
    reread = client.get("/api/recipe/ventes").json()
    output = reread["spec"]["output"]
    assert output["description"] == "nouvelle"
    assert [t["name"] for t in output["columns"][0]["tests"]] == ["not_null"]

    # Et réenregistrer sans y toucher ne rétablit rien d'ancien.
    ok = client.post(
        "/api/recipe/save", json={"spec": reread["spec"], "base": reread["base"]}
    )
    assert ok.status_code == 200, ok.json()
    schema = yaml.safe_load((root / "models" / "schema.yml").read_text())
    model = schema["models"][0]
    assert model["description"] == "nouvelle"
    tests = model["columns"][0].get("tests") or model["columns"][0]["data_tests"]
    assert tests == ["not_null"]


def test_la_doc_suit_le_patch_path_et_pas_le_schema_voisin(fresh_workshop):
    """dbt note dans `patch_path` où une ressource est documentée.

    Écrire ailleurs créait un doublon, et dbt refuse de parser deux
    documentations pour un même modèle.
    """
    client, root = fresh_workshop
    client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})

    # La doc est déplacée à la main dans un autre YAML.
    elsewhere = root / "models" / "docs_ventes.yml"
    elsewhere.write_text(
        "version: 2\nmodels:\n  - name: ventes\n    description: rangée ailleurs\n"
    )
    assert client.get("/api/flow?refresh=true").status_code == 200

    read_value = client.get("/api/recipe/ventes").json()
    assert read_value["base"]["schema_path"] == "models/docs_ventes.yml"
    assert read_value["spec"]["output"]["description"] == "rangée ailleurs"

    spec = {**read_value["spec"]}
    spec["output"] = {**spec["output"], "description": "réécrite par la recipe"}
    r = client.post("/api/recipe/save", json={"spec": spec, "base": read_value["base"]})
    assert r.status_code == 200, r.json()
    assert r.json()["parse_error"] is None, "un doublon casserait le parse"
    assert "réécrite par la recipe" in elsewhere.read_text()
    assert not (root / "models" / "schema.yml").exists()


# --------------------------------------------- bascule de projet : isolation


def test_ouvrir_un_projet_pendant_un_apercu_attend_la_fin(
    nested_workshop, tmp_path: Path, monkeypatch
):
    """Un aperçu ne posait aucun verrou et ne levait pas `run.running`.

    La bascule passait au milieu : elle ferme les adaptateurs et fait `os.chdir`
    vers l'autre projet, pendant que l'aperçu continue avec les settings du
    premier. Le partage de l'état global de dbt rend deux invocations
    concurrentes non sûres dans un même process.
    """
    client, a = nested_workshop
    b = _fresh_project(tmp_path / "b", "atelier_b")
    client.post(
        "/api/recipe/save",
        json={"spec": {"name": "ventes", "type": "sql", "sql": "select 1 as x"}},
    )

    input_entry, resume_point = threading.Event(), threading.Event()
    real_query = Warehouse.query

    def slow_query(self, sql: str, limit: int = 100):
        input_entry.set()
        assert resume_point.wait(30), "le test n'a jamais relâché l'aperçu"
        return real_query(self, sql, limit)

    monkeypatch.setattr(Warehouse, "query", slow_query)

    preview: dict = {}
    opening: dict = {}

    def glimpse():
        preview["r"] = client.post(
            "/api/recipe/preview",
            json={
                "spec": {"name": "ventes", "type": "sql", "sql": "select 1 as x"},
                "limit": 5,
            },
        )

    def open_it():
        opening["r"] = client.post("/api/projects/open", json={"path": str(b)})

    preview_task = threading.Thread(target=glimpse)
    preview_task.start()
    assert input_entry.wait(30), "l'aperçu n'a jamais atteint la lecture"

    open_task = threading.Thread(target=open_it)
    open_task.start()
    time.sleep(1.0)
    assert "r" not in opening, "le projet a changé pendant un aperçu"
    assert os.path.realpath(os.getcwd()) == os.path.realpath(a)

    resume_point.set()
    preview_task.join(60)
    open_task.join(60)

    assert preview["r"].status_code == 200, preview["r"].json()
    assert opening["r"].status_code == 200, opening["r"].json()
    assert opening["r"].json()["opened"] == str(b)


def test_le_dialecte_suit_le_projet_ouvert(nested_workshop, tmp_path: Path):
    """`set_dialect` n'était appelé qu'à la création de l'application.

    Après une bascule, `/api/project` annonçait le nouvel entrepôt pendant que
    la validation des formules jugeait encore avec le dialecte de l'ancien.
    """
    client, _ = nested_workshop
    b = _fresh_project(tmp_path / "b", "atelier_b")
    profile_data = (b / "profiles.yml").read_text()
    assert "type: duckdb" in profile_data
    (b / "profiles.yml").write_text(
        profile_data.replace("type: duckdb", "type: snowflake")
    )

    # Plus de `finally` : la fixture `_duckdb_dialect` borne le réglage, et
    # rend l'entrepôt précédent même si la bascule échoue au milieu.
    assert rcp.current_dialect().sqlglot == "duckdb"
    r = client.post("/api/projects/open", json={"path": str(b)})
    assert r.status_code == 200, r.json()
    assert client.get("/api/project").json()["adapter"] == "snowflake"
    assert (
        rcp.current_dialect().sqlglot == "snowflake"
    ), "la validation juge encore l'ancien"
    assert rcp.current_dialect().adapter == "snowflake"


# ------------------------------------------------- deux onglets, deux projets


def test_une_ecriture_destinee_a_un_autre_projet_est_refusee(
    nested_workshop, tmp_path: Path
):
    """Le projet actif est global au serveur, l'atelier s'ouvre en plusieurs onglets.

    Quand l'onglet B bascule, l'onglet A garde son éditeur ouvert sur le projet
    A : sa sauvegarde suivante partait dans B, et répondait 200. Chaque
    écriture porte donc le projet qu'elle croit modifier, et le serveur compare
    avant d'écrire.
    """
    client, a = nested_workshop
    b = _fresh_project(tmp_path / "b", "atelier_b")

    # L'onglet A a ouvert une recipe pendant que A était le projet actif.
    header = {"X-Pliq-Project": str(a)}
    assert (
        client.post(
            "/api/recipe/save",
            json={"spec": _sql_recipe(), "is_new": True},
            headers=header,
        ).status_code
        == 200
    )

    # L'onglet B bascule.
    assert client.post("/api/projects/open", json={"path": str(b)}).status_code == 200

    # L'onglet A enregistre : rien ne doit être écrit, ni ici ni là-bas.
    r = client.post(
        "/api/recipe/save",
        json={"spec": _sql_recipe(sql="select 2 as x")},
        headers=header,
    )
    assert r.status_code == 409, r.json()
    assert (
        "atelier_b" in r.json()["error"] and "Rien n'a été écrit" in r.json()["error"]
    )
    assert not (b / "models" / "ventes.sql").exists()
    assert "select 1 as x" in (a / "transform" / "models" / "ventes.sql").read_text()

    # Et l'onglet qui s'est recalé sur B, lui, écrit sans histoire.
    ok = client.post(
        "/api/recipe/save",
        json={"spec": _sql_recipe(name="autre"), "is_new": True},
        headers={"X-Pliq-Project": str(b)},
    )
    assert ok.status_code == 200, ok.json()
    assert (b / "models" / "autre.sql").exists()


def test_les_lectures_ne_sont_pas_bloquees_par_l_entete(
    nested_workshop, tmp_path: Path
):
    """Une lecture périmée n'abîme rien : elle s'affiche et se rafraîchit.

    Refuser les GET aussi laisserait l'onglet resté en arrière sans même de quoi
    comprendre ce qui a changé.
    """
    client, a = nested_workshop
    b = _fresh_project(tmp_path / "b", "atelier_b")
    client.post("/api/projects/open", json={"path": str(b)})
    r = client.get("/api/project", headers={"X-Pliq-Project": str(a)})
    assert r.status_code == 200 and r.json()["name"] == "atelier_b"


def test_sans_entete_l_ecriture_passe_comme_avant(fresh_workshop):
    """Un client qui ne dit rien n'est pas cassé : l'en-tête est une garantie
    de plus, pas un passage obligé."""
    client, root = fresh_workshop
    r = client.post("/api/recipe/save", json={"spec": _sql_recipe(), "is_new": True})
    assert r.status_code == 200 and (root / "models" / "ventes.sql").exists()


# --------------------------------------------- supprimer un projet, prudemment


def test_supprimer_un_projet_parent_n_efface_pas_le_projet_ouvert(
    fresh_workshop, tmp_path: Path
):
    """La protection ne comparait que l'égalité des chemins.

    Un projet dbt imbriqué dans un autre — un dépôt qui en contient plusieurs —
    disparaissait avec son parent, et la route répondait 200.
    """
    client, active = fresh_workshop
    parent = active.parent
    write(parent / "dbt_project.yml", (active / "dbt_project.yml").read_text())
    assert projects.is_project(parent)

    r = client.post(
        "/api/projects/forget",
        json={"path": str(parent), "delete_files": True, "confirm": parent.name},
    )
    assert r.status_code == 400, r.json()
    assert "est dans ce dossier" in r.json()["error"]
    assert active.exists() and (active / "dbt_project.yml").exists()


def test_supprimer_un_sous_dossier_du_projet_ouvert_est_refuse(
    fresh_workshop, tmp_path: Path
):
    """`rmtree` sur un projet imbriqué emporte des fichiers du projet ouvert."""
    client, active = fresh_workshop
    inside = active / "sous_projet"
    write(inside / "dbt_project.yml", (active / "dbt_project.yml").read_text())

    r = client.post(
        "/api/projects/forget",
        json={"path": str(inside), "delete_files": True, "confirm": "sous_projet"},
    )
    assert r.status_code == 400 and "dans le projet ouvert" in r.json()["error"]
    assert inside.exists()


def test_supprimer_un_projet_a_cote_marche_toujours(fresh_workshop, tmp_path: Path):
    """Le contre-exemple : ce qui n'a rien à voir avec le projet ouvert s'efface."""
    client, _ = fresh_workshop
    elsewhere = _fresh_project(tmp_path / "ailleurs", "atelier_ailleurs")
    r = client.post(
        "/api/projects/forget",
        json={
            "path": str(elsewhere),
            "delete_files": True,
            "confirm": "atelier_ailleurs",
        },
    )
    assert r.status_code == 200, r.json()
    assert not elsewhere.exists()


# ------------------------------------------------- un modèle déplacé à la main


def test_reenregistrer_un_modele_deplace_ne_le_duplique_pas(fresh_workshop):
    """La lecture suivait le manifeste, l'écriture recalculait le chemin.

    Un modèle déplacé de `staging` vers `marts` se voyait recréé dans
    `staging` : deux fichiers du même nom, et dbt refuse ensuite de parser le
    projet.
    """
    client, root = fresh_workshop
    spec = {**_sql_recipe(), "output": {"layer": "staging"}}
    assert (
        client.post("/api/recipe/save", json={"spec": spec, "is_new": True}).status_code
        == 200
    )

    # Quelqu'un déplace le modèle à la main, et dbt le reparse là où il est.
    before = root / "models" / "staging" / "ventes.sql"
    after = root / "models" / "marts" / "ventes.sql"
    after.parent.mkdir(parents=True, exist_ok=True)
    before.rename(after)
    client.get("/api/flow?refresh=true")

    read_value = client.get("/api/recipe/ventes").json()
    assert read_value["base"]["model_path"] == "models/marts/ventes.sql"

    r = client.post(
        "/api/recipe/save",
        json={"spec": {**spec, "sql": "select 2 as x"}, "base": read_value["base"]},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["path"] == "models/marts/ventes.sql"
    assert not before.exists(), "le modèle ne doit pas renaître à son ancienne place"
    assert "select 2 as x" in after.read_text()
    assert r.json()["parse_error"] is None


def test_changer_la_couche_deplace_le_modele_au_lieu_de_le_recopier(fresh_workshop):
    """Changer la couche dans l'éditeur, c'est demander un déplacement.

    Écrire le neuf sans retirer l'ancien laissait deux modèles du même nom, et
    dbt refusait de parser.
    """
    client, root = fresh_workshop
    spec = {**_sql_recipe(), "output": {"layer": "staging"}}
    client.post("/api/recipe/save", json={"spec": spec, "is_new": True})
    read_value = client.get("/api/recipe/ventes").json()

    r = client.post(
        "/api/recipe/save",
        json={
            "spec": {**spec, "output": {"layer": "marts"}},
            "base": read_value["base"],
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["path"] == "models/marts/ventes.sql"
    assert (root / "models" / "marts" / "ventes.sql").exists()
    assert not (root / "models" / "staging" / "ventes.sql").exists()
    assert r.json()["parse_error"] is None


def test_un_deplacement_n_efface_pas_ce_qui_a_change_entre_temps(fresh_workshop):
    """Le contrôle de conflit comparait le *nouvel* emplacement, toujours vide.

    Un déplacement aurait donc supprimé sans rien dire une retouche faite à la
    main sur l'ancien fichier.
    """
    client, root = fresh_workshop
    spec = {**_sql_recipe(), "output": {"layer": "staging"}}
    client.post("/api/recipe/save", json={"spec": spec, "is_new": True})
    read_value = client.get("/api/recipe/ventes").json()

    old = root / "models" / "staging" / "ventes.sql"
    old.write_text("-- retouché à la main\nselect 99 as x\n")

    r = client.post(
        "/api/recipe/save",
        json={
            "spec": {**spec, "output": {"layer": "marts"}},
            "base": read_value["base"],
        },
    )
    assert r.status_code == 409, r.json()
    assert "a changé" in r.json()["error"]
    assert "retouché à la main" in old.read_text()
    assert not (root / "models" / "marts" / "ventes.sql").exists()


def test_changer_la_couche_n_ecrase_pas_un_modele_deja_la(fresh_workshop):
    """Le déplacement comparait l'ancien fichier, jamais la destination.

    Un modèle écrit à la main dans la couche visée aurait été remplacé sans
    que rien ne le dise — le contrôle de création, lui, était sauté puisque la
    recipe n'est pas neuve.
    """
    client, root = fresh_workshop
    spec = {**_sql_recipe(), "output": {"layer": "staging"}}
    client.post("/api/recipe/save", json={"spec": spec, "is_new": True})
    read_value = client.get("/api/recipe/ventes").json()

    busy = root / "models" / "marts" / "ventes.sql"
    write(busy, "-- logique métier écrite à la main\nselect 42 as reponse\n")

    r = client.post(
        "/api/recipe/save",
        json={
            "spec": {**spec, "output": {"layer": "marts"}},
            "base": read_value["base"],
        },
    )
    assert r.status_code == 409, r.json()
    assert "existe déjà" in r.json()["error"]
    assert "logique métier" in busy.read_text()
    assert (root / "models" / "staging" / "ventes.sql").exists()


# ------------------------------------------------- diagnostic de jointure


JOIN_SPEC = {
    "name": "commandes_clients",
    "type": "join",
    "inputs": [
        {"ref": "stg_orders", "alias": "stg_orders"},
        {"ref": "stg_customers", "alias": "stg_customers"},
    ],
    "joins": [
        {"type": "left", "on": [{"left": "customer_id", "right": "customer_id"}]}
    ],
    "select": [
        {"from": "stg_orders", "column": "order_id", "as": "order_id"},
        {"from": "stg_orders", "column": "amount_eur", "as": "amount_eur"},
        {"from": "stg_customers", "column": "full_name", "as": "full_name"},
    ],
    "output": {"materialized": "view", "layer": "marts"},
}


def test_le_diagnostic_de_jointure_porte_sur_les_tables_entieres(client: TestClient):
    """Cinq commandes, trois clients, une clé unique : rien ne doit multiplier."""
    out = client.post("/api/recipe/diagnose", json={"spec": JOIN_SPEC}).json()
    assert out["scope"] == "complet"
    findings = out["findings"]
    assert findings[0]["level"] == "ok"
    assert "5 en entrée" in findings[0]["title"]
    assert any("est unique sur customer_id" in c["title"] for c in findings)


def test_le_diagnostic_voit_une_jointure_qui_multiplie(client: TestClient):
    """Joindre les commandes sur le client répète chaque client par commande."""
    reversed = {
        **JOIN_SPEC,
        "inputs": [
            {"ref": "stg_customers", "alias": "stg_customers"},
            {"ref": "stg_orders", "alias": "stg_orders"},
        ],
        "select": [{"from": "stg_customers", "column": "customer_id"}],
    }
    findings = client.post("/api/recipe/diagnose", json={"spec": reversed}).json()[
        "findings"
    ]
    assert findings[0]["level"] == "bad"
    assert "multiplie" in findings[0]["title"]
    assert "3 lignes en entrée, 5 en sortie" in findings[0]["detail"]
    assert any("n'est pas unique" in c["title"] for c in findings)


def test_le_diagnostic_refuse_ce_qui_n_est_pas_une_jointure(client: TestClient):
    r = client.post("/api/recipe/diagnose", json={"spec": RECIPE})
    assert r.status_code == 400 and "Joindre" in r.json()["error"]


# ----------------------------------------------------- impact d'un renommage


def test_l_impact_signale_les_colonnes_dont_l_aval_se_sert(
    client: TestClient, project: Settings
):
    """Renommer une colonne casse en silence ce qui la lit. Autant le dire avant."""
    source = {
        "name": "orders_prepares",
        "type": "prepare",
        "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
        "output": {"materialized": "view", "layer": "staging"},
        "steps": [],
    }
    assert client.post(
        "/api/recipe/save", json={"spec": source, "is_new": True}
    ).json()["saved"]

    # Un modèle en aval qui nomme la colonne.
    write(
        project.project_dir / "models" / "marts" / "ca_total.sql",
        "select sum(amount_eur) as total from {{ ref('orders_prepares') }}",
    )
    client.get("/api/flow?refresh=true")

    # Rien ne disparaît : aucun impact.
    assert (
        client.post("/api/recipe/impact", json={"spec": source}).json()["impacts"] == []
    )

    renamed = {
        **source,
        "steps": [
            {
                "id": "r1",
                "enabled": True,
                "type": "rename",
                "params": {"renames": [{"from": "amount_eur", "to": "montant_eur"}]},
            }
        ],
    }
    out = client.post("/api/recipe/impact", json={"spec": renamed}).json()
    assert out["dropped"] == ["amount_eur"]
    # La colonne ne disparaît pas : elle change de nom, et l'atelier le dit —
    # c'est ce qui permet à la modale de proposer de faire suivre l'aval.
    assert out["impacts"] == [
        {
            "column": "amount_eur",
            "used_by": [{"model": "ca_total", "where": "sql"}],
            "renamed_to": "montant_eur",
        }
    ]

    (project.project_dir / "models" / "marts" / "ca_total.sql").unlink()
    client.post("/api/recipe/delete", json={"name": "orders_prepares"})
    client.get("/api/flow?refresh=true")


@pytest.mark.dbt
def test_renommer_une_colonne_ne_reecrit_jamais_l_aval(
    client: TestClient, project: Settings
):
    """L'atelier avertit, il ne répare pas.

    Il l'a fait un temps, et la réécriture était textuelle : elle ignorait la
    portée SQL. Dans un `union all`, chaque branche a la sienne — `amount` n'y
    désigne pas la même colonne — et renommer celle de `a` réécrivait aussi
    celle de `b`, qui ne bougeait pas. Le modèle répondait 200 puis échouait au
    build sur `Referenced column "total" not found`.

    Le champ `rename_columns` de la route a disparu avec : un client qui
    l'envoie encore doit être ignoré, pas obéi.
    """
    upstream = {
        "name": "orders_intacts",
        "type": "prepare",
        "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
        "output": {"materialized": "view", "layer": "staging"},
        "steps": [],
    }
    client.post("/api/recipe/save", json={"spec": upstream, "is_new": True})
    downstream = project.project_dir / "models" / "marts" / "ca_intact.sql"
    write(
        downstream,
        "select amount_eur from {{ ref('orders_intacts') }}\n"
        "union all\n"
        "select amount_eur from {{ ref('stg_orders') }}",
    )
    client.get("/api/flow?refresh=true")

    renamed = {
        **upstream,
        "steps": [
            {
                "id": "r1",
                "enabled": True,
                "type": "rename",
                "params": {"renames": [{"from": "amount_eur", "to": "montant_eur"}]},
            }
        ],
    }
    # L'avertissement, lui, reste : c'est ce que l'atelier sait faire de juste,
    # et c'est ce que l'écran demande avant d'enregistrer.
    impacts = client.post("/api/recipe/impact", json={"spec": renamed}).json()[
        "impacts"
    ]
    assert [i["column"] for i in impacts] == ["amount_eur"]
    assert impacts[0]["renamed_to"] == "montant_eur"
    assert [u["model"] for u in impacts[0]["used_by"]] == ["ca_intact"]

    out = client.post(
        "/api/recipe/save",
        # Le champ n'existe plus : Pydantic l'ignore, et surtout il ne
        # ressuscite pas la réécriture.
        json={"spec": renamed, "rename_columns": {"amount_eur": "montant_eur"}},
    ).json()
    assert out["saved"]
    assert "followed" not in out and "unfollowed" not in out
    assert downstream.read_text().count("amount_eur") == 2, "les deux branches intactes"

    downstream.unlink()
    client.post("/api/recipe/delete", json={"name": "orders_intacts"})
    client.get("/api/flow?refresh=true")


def test_une_recipe_neuve_n_a_rien_a_casser(client: TestClient):
    fresh_spec = {
        "name": "jamais_enregistree",
        "type": "prepare",
        "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
        "output": {"materialized": "view"},
        "steps": [],
    }
    assert (
        client.post("/api/recipe/impact", json={"spec": fresh_spec}).json()["impacts"]
        == []
    )


def test_un_nom_qui_n_est_pas_un_texte_est_refuse_et_non_une_panne(client: TestClient):
    """`spec: dict` laisse passer n'importe quel JSON, y compris un nom nombre.

    La route lisait ce nom avec `validate_name()`, dont le `(name or "").strip()`
    tient le `null` et la chaîne vide mais pas le `42` : l'atelier rendait un
    500 là où il n'avait qu'à refuser une saisie.
    """
    r = client.post(
        "/api/recipe/impact", json={"spec": {"type": "prepare", "name": 42}}
    )
    assert r.status_code == 400 and "texte" in r.json()["error"]


# ----------------------------------------------------- fraîcheur des sources


def test_regler_puis_mesurer_la_fraicheur_d_une_source(
    client: TestClient, project: Settings
):
    """Le parcours entier : on règle le seuil, on lance dbt, le Flow le montre.

    La source est déclarée ici et retirée à la fin : la fixture de session est
    partagée, et un dataset de plus fausserait les tests qui comptent le Flow.
    """
    path = project.project_dir / "models" / "staging" / "sources_fraicheur.yml"
    write(
        path,
        "version: 2\nsources:\n  - name: brut\n    schema: main\n"
        "    tables:\n      - name: commandes\n        identifier: raw_orders\n",
    )
    client.get("/api/flow?refresh=true")
    try:
        uid = uid_of(client, "brut.commandes")
        node_card = client.get(f"/api/dataset/{uid}/tests").json()
        assert node_card["freshness"] == {"loaded_at_field": ""}
        assert node_card["freshness_periods"] == ["minute", "hour", "day"]

        base = {"description": "", "tags": [], "columns": []}

        # Un seuil sans colonne de chargement ne mesure rien sur DuckDB.
        r = client.post(
            f"/api/dataset/{uid}/tests",
            json={**base, "freshness": {"warn_after": {"count": 1, "period": "day"}}},
        )
        assert r.status_code == 400 and "ne mesure rien" in r.json()["error"]

        # dbt veut un horodatage : la colonne est une date, il faut l'expression.
        r = client.post(
            f"/api/dataset/{uid}/tests",
            json={
                **base,
                "freshness": {
                    "loaded_at_field": "cast(ordered_at as timestamp)",
                    "error_after": {"count": 1, "period": "minute"},
                },
            },
        )
        assert r.json()["saved"] is True

        client.post("/api/run", json={"command": "freshness", "select": ""})
        wait_for_run_end(client)

        state = client.get("/api/run").json()["freshness"]
        assert uid in state, f"la fraîcheur doit être relue : {state}"
        assert (
            state[uid]["status"] == "error"
        ), "des données de 2024 dépassent la minute"
        assert state[uid]["max_loaded_at"].startswith("2024-")

        # Elle voyage avec le Flow, et pas avec le statut de build : une source
        # n'est jamais construite, et deux pastilles ne doivent pas dire la
        # même chose sous deux noms différents.
        flow = client.get("/api/flow").json()
        assert flow["freshness"][uid]["status"] == "error"
        assert uid not in flow["states"]
    finally:
        path.unlink()
        client.get("/api/flow?refresh=true")


def test_un_seuil_herite_du_groupe_se_lit_et_se_desactive(
    client: TestClient, project: Settings
):
    """L'écran montrait un formulaire vide là où dbt appliquait un seuil.

    La lecture ne regardait que l'entrée de la table ; dbt, lui, résout d'abord
    ce qu'elle hérite de son groupe. Modifier le seuil répondait 200 sans que le
    manifeste change, et le vider ne désactivait rien — un héritage ne se retire
    pas, il se recouvre.
    """
    # Les propriétés se déclarent sur le *groupe*, pas sur la table : c'est
    # l'héritage que dbt résout depuis toujours. (Leur variante sous `config:`
    # n'existe qu'à partir de dbt 1.10 ; elle se vérifie dans test_files.py,
    # qui n'a pas besoin de faire tourner dbt.)
    path = project.project_dir / "models" / "staging" / "sources_heritage.yml"
    write(
        path,
        "version: 2\nsources:\n  - name: herite\n    schema: main\n"
        "    loaded_at_field: cast(ordered_at as timestamp)\n"
        "    freshness:\n      warn_after: {count: 1, period: hour}\n"
        "    tables:\n      - name: commandes\n        identifier: raw_orders\n",
    )
    client.get("/api/flow?refresh=true")
    try:
        uid = uid_of(client, "herite.commandes")
        node_card = client.get(f"/api/dataset/{uid}/tests").json()
        assert node_card["freshness"] == {
            "loaded_at_field": "cast(ordered_at as timestamp)",
            "warn_after": {"count": 1, "period": "hour"},
        }, "ce que dbt applique, et non ce que la table déclare"

        base = {"description": "", "tags": [], "columns": []}
        r = client.post(
            f"/api/dataset/{uid}/tests",
            json={
                **base,
                "freshness": {"loaded_at_field": "cast(ordered_at as timestamp)"},
            },
        )
        assert r.json()["saved"] is True
        client.get("/api/flow?refresh=true")

        # Le seuil hérité ne s'applique plus : c'est bien dbt qui le dit.
        assert client.get(f"/api/dataset/{uid}/tests").json()["freshness"] == {
            "loaded_at_field": "cast(ordered_at as timestamp)"
        }
    finally:
        path.unlink()
        client.get("/api/flow?refresh=true")


def test_une_commande_deps_ne_porte_pas_de_selecteur(client: TestClient):
    out = client.post(
        "/api/run", json={"command": "deps", "select": "stg_orders"}
    ).json()
    assert out["select"] == ""
    wait_for_run_end(client)


# ------------------------------------ contrôle complet & lignes en échec


def test_le_controle_complet_compte_sur_toute_la_table(client: TestClient):
    """L'échantillon devine, le contrôle complet compte. Les deux ne se valent pas."""
    uid = uid_of(client, "stg_orders")
    out = client.post("/api/profile/full", json={"uid": uid}).json()
    assert out["scope"] == "complet"
    assert out["output"]["total"] == 5
    by_name = {c["name"]: c for c in out["output"]["columns"]}
    assert by_name["order_id"]["distinct"] == 5
    assert by_name["order_id"]["empty"] == 0
    assert by_name["status"]["min"] == "cancelled"
    assert by_name["status"]["max"] == "pending"


def test_le_controle_complet_d_une_recipe_compare_l_entree_et_la_sortie(
    client: TestClient,
):
    """« 2 lignes supprimées, 1 valeur devenue vide » — ce qu'un aperçu tait."""
    spec = {
        "name": "orders_controle",
        "type": "prepare",
        "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
        "output": {"materialized": "view"},
        "steps": [
            {
                "id": "s1",
                "enabled": True,
                "type": "filter_value",
                "params": {
                    "column": "status",
                    "operator": "eq",
                    "values": ["completed"],
                    "action": "keep",
                },
            }
        ],
    }
    out = client.post("/api/profile/full", json={"spec": spec}).json()
    assert out["input"]["total"] == 5
    assert out["output"]["total"] == 3
    assert out["changes"] == [
        "2 lignes supprimées (40.0 %) — 5 en entrée, 3 en sortie."
    ]

    # Vider une cellule ne change pas le nombre de lignes : c'est là que la
    # comparaison des vides a un sens.
    spec["steps"][0]["params"]["action"] = "clear"
    out = client.post("/api/profile/full", json={"spec": spec}).json()
    assert out["output"]["total"] == 5
    assert out["changes"] == ["3 valeurs de « status » sont devenues vides."]


def test_les_lignes_en_echec_d_un_test_sont_consultables(
    client: TestClient, project: Settings
):
    """dbt compile chaque test en une requête qui sélectionne les échecs.

    La rejouer montre exactement les lignes fautives, sans `store_failures`.
    """
    path = project.project_dir / "models" / "staging" / "schema.yml"
    before = path.read_text()
    # `arguments:` n'existe qu'à partir de dbt 1.10.5, et `pyproject.toml`
    # accepte dbt dès 1.9 : écrite en dur, la syntaxe récente faisait échouer ce
    # test sur le job de borne basse — « macro takes no keyword argument
    # 'arguments' ». Le produit choisit déjà selon la version installée
    # (`files._test_entry`) ; le test doit suivre la même règle.
    values_list = (
        "              arguments:\n                values: ['FR']\n"
        if files.dbt_version() >= (1, 10, 5)
        else "              values: ['FR']\n"
    )
    write(
        path,
        before
        + "\n  - name: stg_customers\n    columns:\n"
        + "      - name: country_code\n        tests:\n"
        + "          - accepted_values:\n"
        + values_list,
    )
    client.get("/api/flow?refresh=true")
    try:
        client.post("/api/run", json={"command": "test", "select": "stg_customers"})
        wait_for_run_end(client)

        d = next(
            d
            for d in client.get("/api/flow").json()["datasets"]
            if d["name"] == "stg_customers"
        )
        test = next(t for t in d["tests"] if t["name"] == "accepted_values")

        out = client.post(f"/api/test/{test['id']}/failures", json={"limit": 50}).json()
        assert out["column"] == "country_code"
        names = [c["name"] for c in out["columns"]]
        assert "value_field" in names
        values_list = {r[names.index("value_field")] for r in out["rows"]}
        assert values_list == {"ES"}, "seule la valeur hors liste doit ressortir"
        assert "accepted_values" in out["sql"] or "value_field" in out["sql"]
    finally:
        write(path, before)
        client.get("/api/flow?refresh=true")


def test_un_test_inconnu_repond_404(client: TestClient):
    r = client.post("/api/test/test.inexistant/failures", json={"limit": 10})
    assert r.status_code == 404


def test_un_modele_pas_encore_construit_le_dit(client: TestClient):
    """Un contrôle complet sur un dataset absent de l'entrepôt doit l'expliquer."""
    spec = {
        "name": "jamais_construit",
        "type": "prepare",
        "inputs": [{"ref": "modele_absent", "alias": "modele_absent"}],
        "output": {"materialized": "view"},
        "steps": [],
    }
    r = client.post("/api/profile/full", json={"spec": spec})
    assert r.status_code == 400


# ----------------------------------------------------------------- renommer


def _rename(client: TestClient, old: str, new: str, **extra):
    return client.post(
        "/api/recipe/rename", json={"name": old, "new_name": new, **extra}
    )


def _project_to_rename(client: TestClient, root: Path) -> None:
    """Un amont écrit par une recipe, et trois façons de le nommer en aval."""
    upstream = {
        **_sql_recipe("ventes", "select 1 as x"),
        "output": {"layer": "staging", "description": "Les ventes"},
    }
    assert (
        client.post(
            "/api/recipe/save", json={"spec": upstream, "is_new": True}
        ).status_code
        == 200
    )

    downstream = {
        "name": "ventes_par_mois",
        "type": "sql",
        "sql": "select * from {{ ref('ventes') }}",
        "output": {"layer": "marts"},
    }
    assert (
        client.post(
            "/api/recipe/save", json={"spec": downstream, "is_new": True}
        ).status_code
        == 200
    )

    # Deux lecteurs que l'atelier n'a pas écrits : un modèle à la main, et un
    # test singulier. Ils nomment `ventes` comme les autres.
    write(
        root / "models" / "marts" / "a_la_main.sql",
        "select count(*) as n from {{ ref('ventes') }}\n",
    )
    write(
        root / "tests" / "ventes_non_vides.sql",
        "select 1 from {{ ref('ventes') }} having count(*) = 0\n",
    )
    client.get("/api/flow?refresh=true")


def test_un_lien_hors_du_projet_fait_refuser_le_renommage(
    fresh_workshop, tmp_path: Path
):
    """La règle de confinement vaut aussi pour les fichiers qui *nomment* le modèle.

    `models/consumer.sql` était un lien vers un fichier extérieur : le
    renommage répondait 200 et réécrivait `ref()` chez le voisin, hors de
    l'arbre. Le refus doit arriver avant la première écriture — donc avec le
    modèle toujours en place.
    """
    client, root = fresh_workshop
    _project_to_rename(client, root)

    outside_of = tmp_path / "dehors" / "partage.sql"
    outside_of.parent.mkdir(parents=True, exist_ok=True)
    outside_of.write_text("select * from {{ ref('ventes') }}\n")
    (root / "models" / "marts" / "consumer.sql").symlink_to(outside_of)

    r = _rename(client, "ventes", "chiffre_affaires")
    assert r.status_code == 400, r.json()
    assert "hors" in r.json()["error"]

    # Rien n'a bougé : ni dehors, ni dedans.
    assert "ref('ventes')" in outside_of.read_text()
    assert (root / "models" / "staging" / "ventes.sql").exists()
    assert not (root / "models" / "staging" / "chiffre_affaires.sql").exists()


def test_renommer_un_modele_emmene_tout_ce_qui_le_nommait(fresh_workshop):
    """Le nom d'un modèle est écrit à cinq endroits, et un seul ne suffit pas.

    Renommer le `.sql` seul laisse un projet qui parse encore et ne se construit
    plus — et la sauvegarde suivante depuis l'atelier recrée l'ancien fichier,
    parce qu'elle retrouve le modèle par le nom que porte sa recipe.
    """
    client, root = fresh_workshop
    _project_to_rename(client, root)

    r = _rename(client, "ventes", "chiffre_affaires")
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["parse_error"] is None
    assert out["model_path"] == "models/staging/chiffre_affaires.sql"

    # Le modèle et son script ont bougé, sans rien laisser derrière.
    assert not (root / "models" / "staging" / "ventes.sql").exists()
    assert (root / "models" / "staging" / "chiffre_affaires.sql").exists()
    assert not (root / ".pliq" / "recipes" / "ventes.yml").exists()
    assert (root / ".pliq" / "recipes" / "chiffre_affaires.yml").exists()

    # La doc suit : sinon dbt avertit à chaque parse qu'elle ne documente rien.
    schema = (root / "models" / "staging" / "schema.yml").read_text()
    assert "chiffre_affaires" in schema
    assert "name: ventes" not in schema
    assert "Les ventes" in schema, "la description ne doit pas se perdre en route"

    # Les lecteurs suivent, qu'ils viennent de l'atelier ou pas.
    for rel in (
        "models/marts/a_la_main.sql",
        "models/marts/ventes_par_mois.sql",
        "tests/ventes_non_vides.sql",
        ".pliq/recipes/ventes_par_mois.yml",
    ):
        assert "ref('chiffre_affaires')" in (root / rel).read_text(), rel
    assert set(out["updates"]) == {
        "models/marts/a_la_main.sql",
        "models/marts/ventes_par_mois.sql",
        "tests/ventes_non_vides.sql",
        ".pliq/recipes/ventes_par_mois.yml",
    }

    names = [d["name"] for d in client.get("/api/flow?refresh=true").json()["datasets"]]
    assert "chiffre_affaires" in names and "ventes" not in names


def test_l_en_tete_du_sql_genere_ne_renvoie_pas_vers_un_script_disparu(fresh_workshop):
    """Le SQL généré porte le nom du modèle à un seul endroit : la ligne qui
    renvoie à son script visuel. Elle désignerait sinon un fichier effacé."""
    client, root = fresh_workshop
    _project_to_rename(client, root)
    assert _rename(client, "ventes_par_mois", "ca_mensuel").status_code == 200

    sql = (root / "models" / "marts" / "ca_mensuel.sql").read_text()
    assert f"-- Script visuel : {rcp.RECIPE_DIR}/ca_mensuel.yml" in sql
    assert "ventes_par_mois" not in sql


def test_le_modele_renomme_se_reenregistre_a_sa_nouvelle_place(fresh_workshop):
    """C'est le piège du renommage à la main : la recipe garde l'ancien nom, et
    la sauvegarde suivante recrée le fichier qu'on croyait avoir déplacé."""
    client, root = fresh_workshop
    _project_to_rename(client, root)
    assert _rename(client, "ventes", "chiffre_affaires").status_code == 200

    read_value = client.get("/api/recipe/chiffre_affaires").json()
    assert read_value["spec"]["name"] == "chiffre_affaires"
    r = client.post(
        "/api/recipe/save",
        json={
            "spec": {**read_value["spec"], "sql": "select 2 as x"},
            "base": read_value["base"],
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["path"] == "models/staging/chiffre_affaires.sql"
    assert not (root / "models" / "staging" / "ventes.sql").exists()


def test_le_modele_renomme_se_construit_vraiment(fresh_workshop):
    """Le vrai juge : dbt doit savoir construire ce que le renommage a écrit."""
    client, root = fresh_workshop
    _project_to_rename(client, root)
    assert _rename(client, "ventes", "chiffre_affaires").status_code == 200

    client.post("/api/run", json={"command": "build"})
    state = wait_for_run_end(client)
    assert state["run"]["success"], state["run"]
    release_duckdb_for_test()


def test_l_apercu_dit_ce_qui_bougerait_sans_rien_ecrire(fresh_workshop):
    """L'atelier connaît le DAG : il peut le dire avant d'écrire, pas après."""
    client, root = fresh_workshop
    _project_to_rename(client, root)

    out = _rename(client, "ventes", "chiffre_affaires", dry_run=True).json()
    assert out["renamed"] is False
    assert out["model_path"] == "models/staging/chiffre_affaires.sql"
    assert len(out["updates"]) == 4
    # La doc ne suit pas un `ref()` : elle n'est pas dans `updates`, et
    # l'aperçu sous-compterait s'il ne la nommait pas à part.
    assert out["doc_follows"] is True
    assert out["schema_path"] == "models/staging/schema.yml"
    # dbt ne renomme pas ce qu'il a construit : il construit à côté.
    assert out["old_relation"] and "ventes" in out["old_relation"]

    assert (root / "models" / "staging" / "ventes.sql").exists()
    assert (root / ".pliq" / "recipes" / "ventes.yml").exists()
    assert "ref('ventes')" in (root / "models" / "marts" / "a_la_main.sql").read_text()


def test_un_nom_deja_pris_est_refuse_avant_toute_ecriture(fresh_workshop):
    """Deux ressources du même nom, et dbt refuse de parser le projet entier."""
    client, root = fresh_workshop
    _project_to_rename(client, root)

    r = _rename(client, "ventes", "ventes_par_mois")
    assert r.status_code == 409, r.json()
    assert "déjà pris" in r.json()["error"]
    assert (root / "models" / "staging" / "ventes.sql").exists()
    assert (
        "select * from {{ ref('ventes') }}"
        in (root / "models" / "marts" / "ventes_par_mois.sql").read_text()
    )


def test_un_script_visuel_orphelin_bloque_aussi_le_nom(fresh_workshop):
    """Il ne parse pas, donc le manifeste l'ignore — mais le prochain
    enregistrement écrirait sous ce nom, et écraserait le modèle renommé."""
    client, root = fresh_workshop
    _project_to_rename(client, root)
    write(root / ".pliq" / "recipes" / "deja_pris.yml", "name: deja_pris\ntype: sql\n")

    r = _rename(client, "ventes", "deja_pris")
    assert r.status_code == 409, r.json()
    assert "script visuel" in r.json()["error"]


@pytest.mark.parametrize("wrong", ["ventes", "2ventes", "ventes-bis", "../evade"])
def test_un_nouveau_nom_impossible_est_refuse(fresh_workshop, wrong: str):
    client, root = fresh_workshop
    _project_to_rename(client, root)
    assert _rename(client, "ventes", wrong).status_code == 400
    assert (root / "models" / "staging" / "ventes.sql").exists()


def test_renommer_ce_qui_n_est_pas_un_modele_explique_pourquoi(client: TestClient):
    """Un seed tient son nom de son CSV, une source du nom qu'elle a en base :
    ni l'un ni l'autre ne se renomme comme un modèle."""
    r = _rename(client, "raw_orders", "commandes_brutes")
    assert r.status_code == 404
    assert "seed" in r.json()["error"]

    r = _rename(client, "jamais_vu", "peu_importe")
    assert r.status_code == 404
    assert "Aucun modèle" in r.json()["error"]


def test_renommer_un_modele_ecrit_a_la_main_marche_aussi(fresh_workshop):
    """Un modèle sans script visuel n'a qu'un fichier et sa doc — mais ses
    lecteurs le nomment exactement pareil."""
    client, root = fresh_workshop
    write(root / "models" / "marts" / "metier.sql", "select 1 as x\n")
    write(
        root / "models" / "marts" / "lecteur.sql", "select * from {{ ref('metier') }}\n"
    )
    client.get("/api/flow?refresh=true")

    r = _rename(client, "metier", "metier_v2")
    assert r.status_code == 200, r.json()
    assert r.json()["parse_error"] is None
    assert r.json()["recipe_path"] is None
    assert not (root / "models" / "marts" / "metier.sql").exists()
    assert (
        root / "models" / "marts" / "metier_v2.sql"
    ).read_text() == "select 1 as x\n"
    assert "ref('metier_v2')" in (root / "models" / "marts" / "lecteur.sql").read_text()


def test_un_fichier_deja_la_n_est_pas_ecrase_meme_si_dbt_l_ignore(fresh_workshop):
    """Le manifeste est vide ou périmé quand le projet ne parse plus : le
    contrôle par le manifeste laisse alors passer un fichier bien présent."""
    client, root = fresh_workshop
    _project_to_rename(client, root)
    write(root / "models" / "staging" / "deja_la.sql", "-- du travail à garder\n")

    r = _rename(client, "ventes", "deja_la")
    assert r.status_code == 409, r.json()
    assert "existe déjà" in r.json()["error"]
    assert (
        "du travail à garder"
        in (root / "models" / "staging" / "deja_la.sql").read_text()
    )
    assert (root / "models" / "staging" / "ventes.sql").exists()


# ------------------------- rouvrir un projet ne doit pas changer de cible


def _workshop(root: Path, **options):
    """Un atelier lancé avec des options explicites, comme la ligne de commande."""
    os.chdir(root)
    return TestClient(create_app(load_settings(project_dir=str(root), **options)))


def test_rouvrir_un_projet_garde_les_etats_de_fraicheur(tmp_path: Path):
    """Le démarrage relit la fraîcheur, la bascule l'oubliait.

    `target/sources.json` restait sur le disque, intact ; l'écran perdait
    pourtant ses pastilles d'alerte à la simple réouverture du *même* projet.
    Une source en erreur disparaissait donc sans qu'elle ait cessé de l'être.
    """
    root = _fresh_project(tmp_path / "f", "atelier_f")
    uid = "source.atelier_f.brut.commandes"
    write(
        root / "target" / "sources.json",
        json.dumps(
            {
                "metadata": {"generated_at": "2024-03-01T00:00:00Z"},
                "results": [
                    {
                        "unique_id": uid,
                        "status": "error",
                        "max_loaded_at": "2024-01-01T00:00:00Z",
                    }
                ],
            }
        ),
    )
    with _workshop(root) as client:
        assert client.get("/api/run").json()["freshness"][uid]["status"] == "error"

        assert (
            client.post("/api/projects/open", json={"path": str(root)}).status_code
            == 200
        )
        state = client.get("/api/run").json()["freshness"]
        assert state.get(uid, {}).get("status") == "error", state


def test_un_profiles_dir_relatif_garde_son_sens_a_la_reouverture(tmp_path: Path):
    """Le `chdir` vers le projet tombait entre les deux résolutions.

    L'option de session gardait la chaîne de la ligne de commande, que la
    bascule relisait depuis le projet ouvert : `--profiles-dir profiles` visait
    `<parent>/profiles` au lancement puis `<parent>/demo/profiles` après, un
    dossier qui n'existe pas. L'atelier annonçait alors `adapter: null`.
    """
    root = _fresh_project(tmp_path / "g", "atelier_g")
    profiles_data = tmp_path / "g" / "profiles"
    profiles_data.mkdir(parents=True, exist_ok=True)
    (profiles_data / "profiles.yml").write_text((root / "profiles.yml").read_text())
    (root / "profiles.yml").unlink()

    # Le lancement résout depuis le répertoire d'où on appelle, *puis* se place
    # dans le projet — c'est ce que fait `__main__`, et c'est ce décalage qui
    # faisait changer de sens la chaîne relative à la bascule.
    os.chdir(tmp_path / "g")
    settings = load_settings(project_dir=str(root), profiles_dir="profiles")
    os.chdir(root)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/project").json()["adapter"] == "duckdb"

        assert (
            client.post("/api/projects/open", json={"path": str(root)}).status_code
            == 200
        )
        assert client.get("/api/project").json()["adapter"] == "duckdb"


def test_rouvrir_un_projet_garde_la_cible_du_lancement(tmp_path: Path):
    """La bascule rappelait `load_settings` sans rien reconduire.

    Lancé sur `prod`, l'atelier annonçait `prod` — puis `dev` après une simple
    réouverture du *même* dossier, avec un 200 et sans un mot. Les commandes
    dbt suivantes visaient alors une autre sortie que celle qu'on croyait.
    """
    root = _fresh_project(tmp_path / "p", "atelier_p")
    with _workshop(root, target="prod") as client:
        assert client.get("/api/project").json()["target"] == "prod"

        r = client.post("/api/projects/open", json={"path": str(root)})
        assert r.status_code == 200, r.json()
        assert r.json()["target"] == "prod"
        assert r.json()["target_dropped"] is None
        assert client.get("/api/project").json()["target"] == "prod"


def test_rouvrir_ne_fait_pas_glisser_vers_la_prod_par_defaut(tmp_path: Path):
    """Le cas qui coûte cher : défaut `prod`, lancement explicite en `dev`.

    Oublier le `--target dev` de la session faisait retomber sur le défaut du
    profil — la production — au prochain build.
    """
    root = _fresh_project(tmp_path / "q", "atelier_q")
    yml = (root / "profiles.yml").read_text()
    write(root / "profiles.yml", yml.replace("target: dev", "target: prod"))

    with _workshop(root, target="dev") as client:
        assert client.get("/api/project").json()["target"] == "dev"
        r = client.post("/api/projects/open", json={"path": str(root)})
        assert r.status_code == 200, r.json()
        assert r.json()["target"] == "dev"
        assert client.get("/api/project").json()["target"] == "dev"


def test_une_cible_absente_du_nouveau_projet_est_annoncee(tmp_path: Path):
    """Reconduire la cible ne peut pas être inconditionnel.

    Deux projets n'ont pas les mêmes sorties : pointer `prod` sur un profil qui
    ne la déclare pas ferait échouer chaque commande dbt. On retombe donc sur
    le défaut du profil — mais en le disant, puisque c'est le silence qu'on
    corrige ici.
    """
    a = _fresh_project(tmp_path / "a", "atelier_a")
    b = _fresh_project(tmp_path / "b", "atelier_b")
    yml = (b / "profiles.yml").read_text()
    write(b / "profiles.yml", yml.split("    prod:")[0])  # b n'a plus que dev

    with _workshop(a, target="prod") as client:
        r = client.post("/api/projects/open", json={"path": str(b)})
        assert r.status_code == 200, r.json()
        assert r.json()["target_dropped"] == "prod"
        assert r.json()["target"] == "dev"
        assert client.get("/api/project").json()["target"] == "dev"


def test_rouvrir_un_projet_garde_le_repertoire_de_profils(tmp_path: Path):
    """Un `--profiles-dir` explicite est une option de session, pas de projet.

    L'abandonner faisait chercher le profil dans le nouveau dossier, où il n'y
    en a pas : le projet devenait impossible à rouvrir.
    """
    profiles_data = tmp_path / "profils"
    root = _fresh_project(tmp_path / "r", "atelier_r")
    write(profiles_data / "profiles.yml", (root / "profiles.yml").read_text())
    (root / "profiles.yml").unlink()

    with _workshop(root, profiles_dir=str(profiles_data)) as client:
        assert client.get("/api/project").json()["adapter"] == "duckdb"
        r = client.post("/api/projects/open", json={"path": str(root)})
        assert r.status_code == 200, r.json()
        info = client.get("/api/project").json()
        assert info["adapter"] == "duckdb", "le profil externe doit rester trouvé"
        assert info["targets"] == ["dev", "prod"]


# ------------------- l'éditeur SQL ne doit pas écraser ce qu'il n'a pas lu


def _handwritten_model(client: TestClient, root: Path, sql: str = "select 1 as x\n"):
    """Un modèle écrit à la main, connu de dbt : (uid, chemin du fichier)."""
    f = write(root / "models" / "manual.sql", sql)
    client.get("/api/flow?refresh=true")
    return uid_of(client, "manual"), f


def test_l_editeur_sql_refuse_d_ecraser_une_modification_concurrente(fresh_workshop):
    """La route écrivait sans empreinte ni précondition, contrairement aux recipes.

    On lit le SQL, quelqu'un modifie le fichier dans son éditeur, on enregistre
    la version lue : la réponse était 200 et la modification disparaissait. Le
    verrou de projet sérialise les écritures de l'atelier, il ne voit pas celle
    qui a eu lieu dehors.
    """
    client, root = fresh_workshop
    uid, file = _handwritten_model(client, root)

    read_value = client.get(f"/api/dataset/{uid}").json()
    assert read_value["raw_sql"] == "select 1 as x\n"
    assert read_value["sql_digest"], "la lecture doit rendre de quoi se prouver à jour"

    file.write_text("select 99 as editor_change\n")  # l'éditeur de texte

    r = client.put(
        f"/api/dataset/{uid}/sql",
        json={"sql": "select 2 as x", "base": read_value["sql_digest"]},
    )
    assert r.status_code == 409, r.json()
    assert "a changé" in r.json()["error"]
    assert file.read_text() == "select 99 as editor_change\n"


def test_l_editeur_sql_enregistre_quand_il_est_a_jour(fresh_workshop):
    """Le contrôle ne doit pas gêner le cas normal, ni le deuxième tour."""
    client, root = fresh_workshop
    uid, file = _handwritten_model(client, root)

    read_value = client.get(f"/api/dataset/{uid}").json()
    r = client.put(
        f"/api/dataset/{uid}/sql",
        json={"sql": "select 2 as x", "base": read_value["sql_digest"]},
    )
    assert r.status_code == 200, r.json()
    assert file.read_text() == "select 2 as x\n"

    reread = client.get(f"/api/dataset/{uid}").json()
    assert reread["sql_digest"] != read_value["sql_digest"]
    sequence = client.put(
        f"/api/dataset/{uid}/sql",
        json={"sql": "select 3 as x", "base": reread["sql_digest"]},
    )
    assert sequence.status_code == 200, sequence.json()


def test_l_enregistrement_du_sql_rend_l_empreinte_de_ce_qu_il_ecrit(fresh_workshop):
    """L'éditeur peut rester ouvert : il repart de ce qu'il vient d'écrire.

    Sans cette empreinte dans la réponse, un éditeur qui ne se ferme pas — celui
    dont l'utilisateur a continué à taper pendant la requête — se croyait en
    conflit avec sa propre sauvegarde au tour suivant.
    """
    client, root = fresh_workshop
    uid, file = _handwritten_model(client, root)

    read_value = client.get(f"/api/dataset/{uid}").json()
    r = client.put(
        f"/api/dataset/{uid}/sql",
        json={"sql": "select 2 as x", "base": read_value["sql_digest"]},
    )
    assert r.status_code == 200, r.json()
    digest = r.json()["digest"]
    assert digest and digest != read_value["sql_digest"]
    assert digest == client.get(f"/api/dataset/{uid}").json()["sql_digest"]

    # Et elle suffit au tour suivant, sans relire la fiche.
    sequence = client.put(
        f"/api/dataset/{uid}/sql", json={"sql": "select 3 as x", "base": digest}
    )
    assert sequence.status_code == 200, sequence.json()
    assert file.read_text() == "select 3 as x\n"


def test_l_editeur_sql_sans_empreinte_passe_comme_avant(fresh_workshop):
    """Un client qui n'en envoie pas n'est pas cassé : c'est une garantie de
    plus, pas un passage obligé."""
    client, root = fresh_workshop
    uid, file = _handwritten_model(client, root)
    r = client.put(f"/api/dataset/{uid}/sql", json={"sql": "select 7 as x"})
    assert r.status_code == 200, r.json()
    assert file.read_text() == "select 7 as x\n"


def test_la_fiche_de_tests_refuse_aussi_une_version_perimee(fresh_workshop):
    """Le même principe pour l'autre formulaire qui réécrit une fiche entière."""
    client, root = fresh_workshop
    uid, _ = _handwritten_model(client, root)

    read_value = client.get(f"/api/dataset/{uid}/tests").json()
    first = client.post(
        f"/api/dataset/{uid}/tests",
        json={
            "description": "écrit ailleurs",
            "columns": [],
            "base": read_value["doc_digest"],
        },
    )
    assert first.status_code == 200, first.json()

    # Le deuxième écran repart d'une empreinte devenue périmée.
    r = client.post(
        f"/api/dataset/{uid}/tests",
        json={
            "description": "ma version",
            "columns": [],
            "base": read_value["doc_digest"],
        },
    )
    assert r.status_code == 409, r.json()
    schema = (root / "models" / "schema.yml").read_text()
    assert "écrit ailleurs" in schema and "ma version" not in schema


# ----------- une recipe neuve ne doit pas dupliquer un modèle d'une autre couche


def test_une_recipe_neuve_ne_duplique_pas_un_modele_d_une_autre_couche(fresh_workshop):
    """Le contrôle regardait le fichier destination, pas le nom dans le projet.

    Un `models/staging/ventes.sql` écrit à la main et une nouvelle recipe
    `ventes` en couche `marts` donnaient deux fichiers et un 200 — puis un
    `DuplicateResourceNameError` : les deux restaient là et le projet ne parsait
    plus. Un nom de modèle est unique dans tout le projet, pas dans son dossier.
    """
    client, root = fresh_workshop
    domain = write(root / "models" / "staging" / "ventes.sql", "select 42 as reponse\n")
    client.get("/api/flow?refresh=true")

    r = client.post(
        "/api/recipe/save",
        json={
            "spec": {**_sql_recipe(), "output": {"layer": "marts"}},
            "is_new": True,
        },
    )
    assert r.status_code == 409, r.json()
    assert "models/staging/ventes.sql" in r.json()["error"]

    assert not (root / "models" / "marts" / "ventes.sql").exists()
    assert not (root / ".pliq" / "recipes" / "ventes.yml").exists()
    assert domain.read_text() == "select 42 as reponse\n"
    assert client.get("/api/project").json()["manifest_error"] is None


def test_le_controle_tient_meme_quand_dbt_ne_voit_pas_le_fichier(fresh_workshop):
    """Le manifeste est vide ou périmé quand le projet ne parse plus : le disque
    doit avoir le dernier mot, comme pour le renommage."""
    client, root = fresh_workshop
    write(root / "models" / "staging" / "ventes.sql", "select 42 as reponse\n")
    # Pas de reparse : le manifeste ignore ce fichier, qui est pourtant là.

    r = client.post(
        "/api/recipe/save",
        json={
            "spec": {**_sql_recipe(), "output": {"layer": "marts"}},
            "is_new": True,
        },
    )
    assert r.status_code == 409, r.json()
    assert "staging/ventes.sql" in r.json()["error"]
    assert not (root / "models" / "marts" / "ventes.sql").exists()


def test_une_recipe_neuve_dans_une_couche_libre_passe_toujours(fresh_workshop):
    """Le contrôle ne doit pas refuser ce qui n'entre en collision avec rien."""
    client, root = fresh_workshop
    r = client.post(
        "/api/recipe/save",
        json={
            "spec": {**_sql_recipe(), "output": {"layer": "marts"}},
            "is_new": True,
        },
    )
    assert r.status_code == 200, r.json()
    assert (root / "models" / "marts" / "ventes.sql").exists()


# ------------------- le WebSocket ne doit pas parler à une page étrangère


def test_le_websocket_refuse_une_origine_etrangere(client: TestClient):
    """Les WebSocket échappent à CORS : le navigateur ouvre et laisse parler.

    Une page quelconque pouvait donc, depuis le navigateur de qui a l'atelier
    ouvert, recevoir le `hello` — état du run, états des nœuds — puis s'abonner
    aux événements, dont les journaux dbt portent des noms de ressources et des
    messages d'erreur du projet. La poignée de main est le seul moment où l'on
    peut encore refuser.
    """
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws", headers={"Origin": "https://untrusted.example"}
        ) as ws:
            ws.receive_json()


def test_le_websocket_accepte_la_page_qu_il_a_lui_meme_servie(client: TestClient):
    """Quel que soit le nom par lequel on joint l'atelier, sa propre page passe."""
    with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_le_websocket_sans_origine_reste_ouvert_aux_outils(client: TestClient):
    """Un navigateur en envoie toujours une : son absence désigne autre chose,
    qui forgerait l'en-tête aussi bien. La refuser ne protégerait de personne."""
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"


def test_un_hote_de_reattachement_dns_est_refuse(client: TestClient):
    """Un nom public détourné vers 127.0.0.1 rendrait « même origine » une page
    étrangère, et le contrôle d'origine tomberait avec."""
    r = client.get("/api/project", headers={"Host": "evil.attacker.example"})
    assert r.status_code == 421, r.text
    assert "ne répond pas sous le nom" in r.json()["error"]


@pytest.mark.parametrize(
    "host",
    [
        "localhost:8765",
        "127.0.0.1:8765",
        "192.168.1.20:8765",
        "macbook.local",
        # Une IPv6 porte ses propres deux-points : c'est le crochet fermant qui
        # borne le nom, pas le dernier « : ».
        "[::1]:8765",
    ],
)
def test_les_hotes_locaux_ordinaires_passent(client: TestClient, host: str):
    """Refuser tout sauf localhost interdirait de joindre l'atelier par le nom
    de sa propre machine — un usage ordinaire, pas une attaque."""
    assert client.get("/api/project", headers={"Host": host}).status_code == 200


# Le contrôle d'hôte ci-dessus ne dit rien du *corps* d'une requête, et c'est
# l'autre moitié de la parade : une page étrangère ne peut pas lire la réponse
# de l'atelier, mais elle pourrait déclencher l'écriture si le corps passait.
# Ce qui l'en empêche est la borne `fastapi>=0.132` de `pyproject.toml`, pas du
# code d'ici — d'où ces tests, qui la vérifient par son effet.


@pytest.mark.parametrize(
    "headers",
    [
        # Les trois types de contenu qu'un navigateur envoie sans contrôle CORS
        # préalable, plus l'absence pure et simple — le cas du `Blob` sans type.
        {},
        {"Content-Type": "text/plain;charset=UTF-8"},
        {"Content-Type": "application/x-www-form-urlencoded"},
        {"Content-Type": "multipart/form-data; boundary=x"},
    ],
)
def test_une_ecriture_sans_content_type_json_est_refusee(
    client: TestClient, headers: dict
):
    """Le corps est du JSON valide et la route existe : seul le type manque.

    Un 404 signerait la régression — il voudrait dire que le corps a été lu et
    que la route a cherché la recipe. Le nom visé n'existe pas, pour que même
    une régression ne détruise rien.
    """
    r = client.post(
        "/api/recipe/delete",
        content=b'{"name":"aucun_modele_de_ce_nom"}',
        headers={"Origin": "https://untrusted.example", **headers},
    )
    assert r.status_code == 422, r.text


def test_la_borne_basse_de_fastapi_porte_la_protection():
    """`pyproject.toml` annonce `fastapi>=0.132`, et c'est cette version qui
    refuse un corps sans `Content-Type`. Installé plus vieux, l'atelier
    s'ouvrirait aux écritures étrangères sans que rien ne le dise : le job CI
    « borne basse » installe justement le plus vieux FastAPI annoncé."""
    from importlib.metadata import version

    installed = tuple(int(n) for n in version("fastapi").split(".")[:2])
    assert installed >= (0, 132), f"FastAPI {version('fastapi')} est trop ancien"


@pytest.mark.parametrize("origin", ["null", "file://", "https://untrusted.example"])
def test_aucune_origine_etrangere_n_ouvre_le_websocket(client: TestClient, origin: str):
    """« null » est l'origine d'une iframe bac à sable : ce n'est pas « absente »."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"Origin": origin}) as ws:
            ws.receive_json()


# ===================================================================
# La cible de session survit aux bascules
# ===================================================================


def test_la_cible_du_lancement_survit_a_un_detour_par_un_autre_projet(tmp_path: Path):
    """A → B → A : la cible demandée au lancement était définitivement perdue.

    La bascule reconduisait `settings.target`, puis le remplaçait par `None`
    quand le projet suivant ne déclarait pas cette cible — et ce `Settings`-là
    devenait la configuration de référence de la session. Le passage par un
    projet qui ne connaît pas `dev` effaçait donc le `--target dev` du
    lancement : au retour sur A, dont le profil a `prod` par défaut, l'atelier
    annonçait `prod` avec un 200 et `target_dropped: null`. La commande dbt
    suivante visait la production.
    """
    a = _fresh_project(tmp_path / "a", "atelier_a")
    yml = (a / "profiles.yml").read_text()
    write(a / "profiles.yml", yml.replace("target: dev", "target: prod"))

    b = _fresh_project(tmp_path / "b", "atelier_b")
    ybis = (b / "profiles.yml").read_text()
    # B ne connaît que `sandbox` : ni `dev` ni `prod`.
    write(
        b / "profiles.yml",
        ybis.split("    prod:")[0]
        .replace("    dev:", "    sandbox:")
        .replace("target: dev", "target: sandbox"),
    )

    with _workshop(a, target="dev") as client:
        assert client.get("/api/project").json()["target"] == "dev"

        # Le détour : B ne déclare pas `dev`, le repli est annoncé.
        r = client.post("/api/projects/open", json={"path": str(b)})
        assert r.status_code == 200, r.json()
        assert r.json()["target_dropped"] == "dev"
        assert r.json()["target"] == "sandbox"

        # Le retour : c'est ici que la demande se perdait.
        r = client.post("/api/projects/open", json={"path": str(a)})
        assert r.status_code == 200, r.json()
        assert r.json()["target"] == "dev", "le --target dev du lancement"
        assert r.json()["target_dropped"] is None
        assert client.get("/api/project").json()["target"] == "dev"


def test_un_detour_ne_transforme_pas_une_cible_abandonnee_en_choix(tmp_path: Path):
    """Le repli vaut pour le projet qui l'impose, et pour lui seul.

    Sans cible demandée, chaque projet garde le défaut de son profil : le
    repli d'un projet ne doit pas devenir la cible de session des suivants.
    """
    a = _fresh_project(tmp_path / "c", "atelier_c")
    b = _fresh_project(tmp_path / "d", "atelier_d")
    ybis = (b / "profiles.yml").read_text()
    write(b / "profiles.yml", ybis.split("    prod:")[0])  # b n'a que dev

    with _workshop(a, target="prod") as client:
        assert (
            client.post("/api/projects/open", json={"path": str(b)}).json()[
                "target_dropped"
            ]
            == "prod"
        )
        r = client.post("/api/projects/open", json={"path": str(a)})
        assert r.json()["target"] == "prod", "A la déclare : on y revient"


# ===================================================================
# Les actions de recipe portent une identité, pas un nom
# ===================================================================


def test_la_recipe_d_un_modele_de_paquet_n_est_pas_celle_du_projet(
    workshop_with_package,
):
    """Les scripts visuels étaient associés aux modèles par leur seul nom.

    Le `orders` du paquet héritait donc de la recipe locale `orders`, annoncée
    `managed: true`, avec toutes ses actions — qui portaient en réalité sur le
    modèle local.
    """
    client, root = workshop_with_package
    rcp.save_recipe(
        root, {"name": "orders", "type": "prepare", "steps": [], "inputs": []}
    )
    recipes = {r["model"]: r for r in client.get("/api/flow").json()["recipes"]}

    assert recipes["model.atelier_p.orders"]["managed"] is True
    assert recipes["model.atelier_p.orders"]["external"] is False

    from_package = recipes["model.vendor.orders"]
    assert from_package["managed"] is False, "le script visuel est au projet"
    assert from_package["external"] is True
    assert from_package["editable"] is False
    assert from_package["package"] == "vendor"


def test_supprimer_depuis_la_recipe_d_un_paquet_epargne_le_modele_local(
    workshop_with_package,
):
    """Le constat le plus coûteux de l'audit : 200, et le fichier local effacé.

    La modale envoyait `{"name": "orders"}`, et le serveur prenait le premier
    modèle homonyme du manifest sans regarder son paquet.
    """
    client, root = workshop_with_package
    local = root / "models" / "orders.sql"

    r = client.post(
        "/api/recipe/delete",
        json={
            "name": "orders",
            "delete_model": True,
            "model_uid": _uid_at(client, "orders", "vendor"),
        },
    )
    assert r.status_code == 403, r.json()
    assert "vendor" in r.json()["error"]
    assert local.read_text() == "select 2 as ecrit_par_moi\n", "intact"


def test_ouvrir_la_recipe_d_un_paquet_ne_charge_pas_le_script_local(
    workshop_with_package,
):
    """La route est adressée par nom : elle rendait le script du projet."""
    client, root = workshop_with_package
    rcp.save_recipe(
        root, {"name": "orders", "type": "prepare", "steps": [], "inputs": []}
    )
    uid = _uid_at(client, "orders", "vendor")

    r = client.get(f"/api/recipe/orders?model_uid={uid}")
    assert r.status_code == 403, r.json()

    # Celui du projet s'ouvre toujours.
    local = _uid_at(client, "orders", "atelier_p")
    assert client.get(f"/api/recipe/orders?model_uid={local}").status_code == 200


def test_renommer_depuis_un_modele_de_paquet_est_refuse(workshop_with_package):
    """Même porte, même refus — et l'aperçu n'écrit rien non plus."""
    client, root = workshop_with_package
    uid = _uid_at(client, "orders", "vendor")
    for sec in (True, False):
        r = client.post(
            "/api/recipe/rename",
            json={
                "name": "orders",
                "new_name": "commandes",
                "dry_run": sec,
                "model_uid": uid,
            },
        )
        assert r.status_code == 403, r.json()
    assert (root / "models" / "orders.sql").exists(), "rien n'a bougé"
    assert not (root / "models" / "commandes.sql").exists()


def test_une_identite_qui_ne_correspond_pas_au_nom_est_refusee(workshop_with_package):
    """Un Flow périmé désigne un modèle, la requête en nomme un autre."""
    client, root = workshop_with_package
    write(root / "models" / "autre.sql", "select 9 as autre\n")
    client.get("/api/flow?refresh=true")
    other = _uid_at(client, "autre", "atelier_p")

    r = client.post(
        "/api/recipe/delete",
        json={"name": "orders", "delete_model": True, "model_uid": other},
    )
    assert r.status_code == 409, r.json()
    assert (root / "models" / "orders.sql").exists()


def test_supprimer_le_modele_du_projet_marche_toujours(workshop_with_package):
    """Le garde-fou ne doit pas fermer la porte au projet lui-même."""
    client, root = workshop_with_package
    local = root / "models" / "orders.sql"
    assert local.exists()

    r = client.post(
        "/api/recipe/delete",
        json={
            "name": "orders",
            "delete_model": True,
            "model_uid": _uid_at(client, "orders", "atelier_p"),
        },
    )
    assert r.status_code == 200, r.json()
    assert not local.exists()


def test_le_nom_seul_ne_designe_jamais_un_modele_de_paquet(workshop_with_package):
    """Sans identité, la résolution par nom reste dans le projet ouvert.

    C'est l'autre moitié du correctif : un client qui n'envoie pas d'identité
    ne doit pas pouvoir atteindre un fichier de paquet par un homonyme.
    """
    client, root = workshop_with_package
    r = client.post(
        "/api/recipe/rename",
        json={"name": "orders", "new_name": "commandes", "dry_run": True},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["model_path"].startswith("models/"), r.json()
    # La table que l'aperçu laisserait derrière dit lequel des deux `orders` a
    # été résolu : le paquet écrit dans `orders_vendor`, le projet dans
    # `orders`. C'est le seul endroit de la réponse où les deux se distinguent.
    assert "orders_vendor" not in (r.json()["old_relation"] or "")
    assert (root / "models" / "orders.sql").read_text() == "select 2 as ecrit_par_moi\n"


# ===================================================================
# Les fiches d'un modèle versionné
# ===================================================================


def test_la_fiche_d_une_version_montre_la_description_de_cette_version(
    versioned_workshop,
):
    """La lecture rendait l'entrée commune, jamais celle de la version.

    L'URL identifie bien `model.atelier_v.customers.v1`, mais les fonctions de
    documentation ne recevaient que `node.name` : elles lisaient
    `models[name=orders]` et rendaient une description vide, ou celle d'une
    autre version.
    """
    client, _ = versioned_workshop

    v1 = client.get("/api/dataset/model.atelier_v.customers.v1/tests")
    assert v1.status_code == 200, v1.json()
    assert v1.json()["version"] == "1"
    assert v1.json()["description"] == "version une"

    v2 = client.get("/api/dataset/model.atelier_v.customers.v2/tests")
    assert v2.json()["version"] == "2"
    assert v2.json()["description"] == "version deux"

    # Les colonnes aussi : la v1 surcharge la description commune de `id`.
    by_name = {c["name"]: c for c in v1.json()["columns"]}
    assert by_name["id"]["description"] == "identifiant de la v1"


def test_enregistrer_la_fiche_d_une_version_est_refuse(versioned_workshop):
    """Écrire répondait 200 en modifiant la propriété commune.

    La description spécifique de la v1 continuait de la masquer — rien ne
    changeait pour elle — pendant que la fiche de la v2, elle, se mettait à
    afficher ce qu'on venait d'écrire « pour la v1 ». L'atelier annonçait une
    modification qui ne correspondait à aucune propriété effective.
    """
    client, root = versioned_workshop
    yml = root / "models" / "versions.yml"
    before = yml.read_text()
    uid = "model.atelier_v.customers.v1"

    base = client.get(f"/api/dataset/{uid}/tests").json()
    r = client.post(
        f"/api/dataset/{uid}/tests",
        json={
            "description": "modification v1",
            "columns": [],
            "tags": [],
            "base": base["doc_digest"],
        },
    )
    assert r.status_code == 409, r.json()
    assert "versionné" in r.json()["error"]

    doc = client.post(
        f"/api/dataset/{uid}/doc",
        json={"description": "modification v1", "columns": []},
    )
    assert doc.status_code == 409, doc.json()

    assert yml.read_text() == before, "le YAML n'a pas bougé"
    # Et la v2 n'a rien attrapé au passage.
    assert (
        client.get("/api/dataset/model.atelier_v.customers.v2/tests").json()[
            "description"
        ]
        == "version deux"
    )


def test_la_fiche_d_une_version_s_annonce_en_lecture_seule(versioned_workshop):
    """L'écran doit le dire avant la frappe, pas après l'envoi."""
    client, _ = versioned_workshop
    node_card = client.get("/api/dataset/model.atelier_v.customers.v1/tests").json()

    assert node_card["editable"] is False
    assert "version" in (node_card["readonly_reason"] or "").lower()


def test_un_modele_sans_version_reste_editable(versioned_workshop):
    """Le garde-fou ne porte que sur les modèles versionnés."""
    client, root = versioned_workshop
    write(root / "models" / "simple.sql", "select 1 as id\n")
    client.get("/api/flow?refresh=true")

    uid = "model.atelier_v.simple"
    node_card = client.get(f"/api/dataset/{uid}/tests").json()
    assert node_card["editable"] is True
    assert node_card["version"] == ""
    assert node_card["readonly_reason"] is None

    r = client.post(
        f"/api/dataset/{uid}/tests",
        json={
            "description": "un modèle ordinaire",
            "columns": [],
            "tags": [],
            "base": node_card["doc_digest"],
        },
    )
    assert r.status_code == 200, r.json()


def test_une_recipe_neuve_retient_sa_propre_identite(workshop_with_package):
    """L'identité rendue à l'enregistrement doit être celle du projet.

    `recipe_save` balayait tout le manifeste par le seul nom — paquets
    compris, tous types confondus — et rendait le premier homonyme. Créer une
    recipe sous un nom que seul un paquet déclare rendait donc l'`unique_id` du
    paquet ; l'éditeur le retenait, et l'enregistrement suivant se voyait
    refusé en 403 avec un message parlant d'un paquet que personne n'avait
    visé. La recipe devenait inutilisable depuis l'écran qui venait de la créer.
    """
    client, root = workshop_with_package
    spec = {"name": "orders_bis", "type": "sql", "sql": "select 1 as x", "steps": []}

    # Le paquet déclare déjà `orders` : on crée une recipe qui produira un
    # modèle local du même nom que lui.
    spec["name"] = "orders"
    (root / "models" / "orders.sql").unlink()
    r = client.post("/api/recipe/save", json={"spec": spec, "is_new": True})
    assert r.status_code == 200, r.json()
    assert r.json()["unique_id"] == "model.atelier_p.orders", r.json()

    # Et la deuxième écriture, qui rend cette identité, passe.
    still_there = client.post(
        "/api/recipe/save",
        json={
            "spec": spec,
            "base": r.json()["base"],
            "model_uid": r.json()["unique_id"],
        },
    )
    assert still_there.status_code == 200, still_there.json()


# ===================================================================
# Le champ sélecteur ne prend que des sélecteurs
# ===================================================================


def test_le_champ_selecteur_refuse_une_option_dbt(workshop_with_package):
    """`select` et `exclude` étaient poussés morceau par morceau sur la ligne
    de commande dbt, sans aucun contrôle. Un morceau qui commence par `--` n'est
    plus un sélecteur : c'est une option. `m --target prod` tapé dans le champ
    du bandeau construisait un build sur la production — et comme les arguments
    de session sont ajoutés après mais ne portent `--target` que si la session
    en a un, un atelier lancé sans `--target` n'avait rien à opposer.
    """
    client, root = workshop_with_package
    before = sorted(p.name for p in root.glob("*.duckdb"))

    for field in ("select", "exclude"):
        r = client.post("/api/run", json={"command": "build", field: "m --target prod"})
        assert r.status_code == 400, r.json()
        assert "sélecteur" in r.json()["error"]

    # Et les formes courtes, qui passent tout autant sur une ligne de commande.
    r = client.post("/api/run", json={"command": "build", "select": "-t prod"})
    assert r.status_code == 400, r.json()

    assert sorted(p.name for p in root.glob("*.duckdb")) == before, "aucune base créée"


def test_un_vrai_selecteur_passe_toujours(workshop_with_package):
    """Le garde-fou ne doit pas fermer le champ à ce qu'il sert à écrire."""
    client, _ = workshop_with_package
    for selector in ("orders", "tag:finance", "stg_orders+", "path:models/marts"):
        r = client.post("/api/run", json={"command": "compile", "select": selector})
        assert r.status_code == 200, (selector, r.json())
        # Un run à la fois : on attend la fin avant le sélecteur suivant.
        end = time.time() + 60
        while client.get("/api/run").json()["run"]["running"] and time.time() < end:
            time.sleep(0.02)


# ===================================================================
# La fiche d'un modèle de paquet ne vient pas du projet ouvert
# ===================================================================


def test_la_fiche_d_un_modele_de_paquet_ne_lit_pas_le_yaml_local(
    workshop_with_package,
):
    """`patch_path` est relatif à la racine du paquet qui déclare la ressource.

    `vendor://models/schema.yml` résolu depuis le projet ouvert désignait le
    `schema.yml` local : la fiche du modèle de paquet annonçait la description,
    les tags, les colonnes et les tests du modèle local, et nommait un fichier
    de ce projet comme étant le sien. La route voisine `GET /api/dataset/{uid}`,
    qui lit le nœud, disait l'inverse sur le même dataset.
    """
    client, root = workshop_with_package
    write(
        root / "models" / "schema.yml",
        "version: 2\nmodels:\n  - name: orders\n"
        "    description: la fiche du PROJET LOCAL\n"
        "    config:\n      tags: [secret_local]\n"
        "    columns:\n      - name: ecrit_par_moi\n"
        "        description: colonne du projet\n"
        "        tests: [not_null]\n",
    )
    write(
        root / "dbt_packages" / "vendor" / "models" / "schema.yml",
        "version: 2\nmodels:\n  - name: orders\n    description: la fiche du PAQUET\n",
    )
    client.get("/api/flow?refresh=true")

    node_card = client.get(
        f"/api/dataset/{_uid_at(client, 'orders', 'vendor')}/tests"
    ).json()

    assert node_card["description"] == "la fiche du PAQUET"
    assert "secret_local" not in node_card["tags"]
    assert node_card["schema_path"] is None, "aucun fichier de ce projet n'est le sien"
    assert node_card["doc_digest"] is None
    assert node_card["editable"] is False
    assert "paquet" in (node_card["readonly_reason"] or "")
    names = {c["name"] for c in node_card["columns"]}
    assert "ecrit_par_moi" not in names, "la colonne du modèle local"
    assert not any(c["tests"] for c in node_card["columns"]), "ni ses tests"

    # Les deux routes doivent dire la même chose du même nœud.
    detail = client.get(f"/api/dataset/{_uid_at(client, 'orders', 'vendor')}").json()
    assert detail["description"] == node_card["description"]


def test_la_fiche_du_modele_local_reste_celle_du_projet(workshop_with_package):
    """Le contre-exemple : le projet garde la sienne, éditable et située."""
    client, root = workshop_with_package
    write(
        root / "models" / "schema.yml",
        "version: 2\nmodels:\n  - name: orders\n"
        "    description: la fiche du PROJET LOCAL\n"
        "    columns:\n      - name: ecrit_par_moi\n"
        "        description: colonne du projet\n"
        "        tests: [not_null]\n",
    )
    client.get("/api/flow?refresh=true")

    node_card = client.get(
        f"/api/dataset/{_uid_at(client, 'orders', 'atelier_p')}/tests"
    ).json()
    assert node_card["description"] == "la fiche du PROJET LOCAL"
    assert node_card["schema_path"] == "models/schema.yml"
    assert node_card["doc_digest"]
    assert node_card["editable"] is True
    by_name = {c["name"]: c for c in node_card["columns"]}
    assert by_name["ecrit_par_moi"]["description"] == "colonne du projet"
    assert [t["name"] for t in by_name["ecrit_par_moi"]["tests"]] == ["not_null"]


# ------------------------------- renommer sans changer de langage (C3)


PY_MODEL = """\
def model(dbt, session):
    dbt.config(materialized="table")
    return session.sql("select 1 as x")
"""


def test_renommer_un_modele_python_garde_son_extension(tmp_path: Path):
    """Le nouveau chemin recevait `.sql`, quel que soit le fichier d'origine.

    `pymodel.py` devenait `renomme.sql` : l'API répondait succès — il n'y a
    rien à analyser dans un `.sql` — et c'est le build suivant qui butait sur
    `def model(...)`. dbt lit le langage d'un modèle dans son extension.
    """
    root = _fresh_project(tmp_path / "py", "atelier_py")
    write(root / "models" / "staging" / "pymodel.py", PY_MODEL)
    os.chdir(root)

    with TestClient(create_app(load_settings(project_dir=str(root)))) as c:
        r = _rename(c, "pymodel", "renomme")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["renamed"] is True
        assert body["model_path"] == "models/staging/renomme.py"
        assert body["parse_error"] is None, "le projet doit encore parser"

    assert not (root / "models" / "staging" / "pymodel.py").exists()
    assert not (root / "models" / "staging" / "renomme.sql").exists()
    new = root / "models" / "staging" / "renomme.py"
    assert new.exists()
    assert new.read_text() == PY_MODEL, "le code Python est rendu intact"


def test_renommer_un_modele_sql_reste_un_modele_sql(tmp_path: Path):
    """Le revers : l'extension suivie est celle du fichier, pas une nouveauté."""
    root = _fresh_project(tmp_path / "sq", "atelier_sq")
    write(root / "models" / "staging" / "stg_x.sql", "select 1 as x\n")
    os.chdir(root)

    with TestClient(create_app(load_settings(project_dir=str(root)))) as c:
        r = _rename(c, "stg_x", "stg_y")
        assert r.status_code == 200, r.text
        assert r.json()["model_path"] == "models/staging/stg_y.sql"
    assert (root / "models" / "staging" / "stg_y.sql").exists()


# ------------------------------------------- forme du script reçu par l'API
#
# `spec: dict` accepte tout objet JSON. Ce qui traversait la validation
# s'arrêtait au premier `.get()` sur un `None`, c'est-à-dire en 500 — une
# panne annoncée pour ce qui n'est qu'une saisie à refuser.

_MALFORMED_WRAPPERS = [
    {"type": "prepare", "inputs": [None]},
    {"type": "prepare", "inputs": ["stg_orders"]},
    {"type": "prepare", "inputs": "stg_orders"},
    {"type": "prepare", "inputs": [{"ref": "stg_orders"}], "steps": [None]},
    {"type": "prepare", "inputs": [{"ref": "stg_orders"}], "steps": "drop"},
    {"type": "prepare", "inputs": [{"ref": "stg_orders"}], "output": "table"},
    {"type": [], "inputs": [{"ref": "stg_orders"}]},
    "prepare",
    [],
    # Un étage plus bas : les réglages d'une étape que le processeur parcourt
    # comme des objets. `params` était jugé « c'est bien un objet », et le
    # reste supposé bien formé jusqu'à `r.get("from")`.
    {
        "type": "prepare",
        "inputs": [{"ref": "stg_orders"}],
        "steps": [{"type": "rename", "params": {"renames": [None]}}],
    },
    {
        "type": "prepare",
        "inputs": [{"ref": "stg_orders"}],
        "steps": [{"type": "rename", "params": {"renames": ["order_id"]}}],
    },
    {
        "type": "prepare",
        "inputs": [{"ref": "stg_orders"}],
        "steps": [{"type": "rename", "params": {"renames": {"from": "a", "to": "b"}}}],
    },
]


@pytest.mark.parametrize(
    "route", ["columns", "compile", "preview", "diagnose", "impact", "save"]
)
@pytest.mark.parametrize("spec", _MALFORMED_WRAPPERS)
def test_un_script_malforme_est_refuse_et_ne_casse_pas_la_route(
    client: TestClient, route: str, spec
):
    r = client.post(f"/api/recipe/{route}", json={"spec": spec})
    assert r.status_code < 500, f"{route} rend une panne pour une saisie : {r.text}"
    # Le refus doit aussi être *lisible* : l'écran n'affiche que `error`.
    if r.status_code == 400:
        assert r.json().get("error"), r.text


@pytest.mark.parametrize("spec", _MALFORMED_WRAPPERS)
def test_un_script_malforme_ne_casse_pas_le_profil_complet(client: TestClient, spec):
    r = client.post("/api/profile/full", json={"spec": spec})
    assert r.status_code < 500, r.text


def test_le_refus_d_un_script_malforme_situe_la_faute(client: TestClient):
    """Sur dix entrées, « objet attendu » sans numéro ne se corrige pas."""
    r = client.post(
        "/api/recipe/compile",
        json={"spec": {"type": "prepare", "inputs": [{"ref": "stg_orders"}, None]}},
    )
    assert r.status_code == 400, r.text
    assert "n° 2" in r.json()["error"]

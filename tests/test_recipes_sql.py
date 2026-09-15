"""Le fichier .sql produit : en-tête, config, CTE, et ce que l'interface en lit.

Le `.sql` est la source de vérité du projet dbt : il doit rester lisible et
contenir exactement ce que dbt attend.
"""

from __future__ import annotations

import pytest
import sqlglot
from jinja2 import Environment

from pliq import full_check
from pliq import recipes as rcp

from .conftest import Bench, prepare_spec, resolve_refs_for_test

# ------------------------------------------------------------------- config


def test_une_view_choisie_s_ecrit_une_recipe_muette_non():
    """Choisir « view » doit s'écrire : la couche peut matérialiser en table.

    Sans `materialized='view'` dans le modèle, un `+materialized: table` posé
    sur la couche dans dbt_project.yml gagne, et le choix de l'utilisateur est
    silencieusement renversé. Une recipe qui ne demande rien, elle, laisse bien
    la main au projet.
    """
    assert "materialized='view'" in rcp.config_block({"materialized": "view"})
    assert rcp.config_block({}) == ""


def test_table_et_tags():
    out = rcp.config_block({"materialized": "table", "tags": ["finance", " "]})
    assert "materialized='table'" in out and "tags=['finance']" in out


def test_incremental_complet():
    out = rcp.config_block(
        {
            "materialized": "incremental",
            "unique_key": ["order_id", "line_id"],
            "incremental_strategy": "merge",
            "on_schema_change": "sync_all_columns",
        }
    )
    assert "unique_key=['order_id', 'line_id']" in out
    assert "incremental_strategy='merge'" in out
    assert "on_schema_change='sync_all_columns'" in out


def test_une_seule_cle_unique_reste_une_chaine():
    out = rcp.config_block(
        {
            "materialized": "incremental",
            "unique_key": ["order_id"],
            "incremental_strategy": "delete+insert",
        }
    )
    assert "unique_key='order_id'" in out


def test_le_filtre_incremental_n_est_ecrit_que_si_une_colonne_de_repere_existe():
    base = {"materialized": "incremental", "incremental_strategy": "append"}
    assert rcp.incremental_filter(base) == ""
    assert rcp.incremental_filter(
        {**base, "incremental": {"column": "ordered_at"}}
    ).strip()
    # La borne large se lit avec une stratégie qui remplace : en « append »
    # elle réempile la ligne assise sur le maximum, et c'est refusé.
    assert ">= (select max" in rcp.incremental_filter(
        {
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "incremental": {"column": "ordered_at", "operator": "gte"},
        }
    )
    assert (
        rcp.incremental_filter(
            {"materialized": "table", "incremental": {"column": "ordered_at"}}
        )
        == ""
    )


# --------------------------------------------------------------- références


def test_ref_et_source():
    assert rcp.ref_sql({"ref": "stg_orders"}) == "{{ ref('stg_orders') }}"
    assert (
        rcp.ref_sql({"source_name": "raw", "table": "orders"})
        == "{{ source('raw', 'orders') }}"
    )


def test_un_ref_porte_le_paquet_et_la_version_de_l_entree():
    """Le nom seul ne désigne pas un dataset.

    Deux paquets peuvent déclarer `orders` : `ref('orders')` nu rend celui du
    projet ouvert, quel que soit celui qu'on a choisi à l'écran. Et
    `ref('customers')` rend la dernière version, même quand c'est la v1 qui a
    été désignée.
    """
    assert (
        rcp.ref_sql({"ref": "orders", "package": "vendor"})
        == "{{ ref('vendor', 'orders') }}"
    )
    assert (
        rcp.ref_sql({"ref": "customers", "version": 1}) == "{{ ref('customers', v=1) }}"
    )
    assert (
        rcp.ref_sql({"ref": "customers", "version": "beta"})
        == "{{ ref('customers', v='beta') }}"
    )
    assert (
        rcp.ref_sql({"ref": "orders", "package": "vendor", "version": "2"})
        == "{{ ref('vendor', 'orders', v=2) }}"
    )
    # Ce qui n'est pas écrit ne s'invente pas : une entrée sans paquet ni
    # version reste ce que les recipes déjà enregistrées contiennent.
    assert rcp.ref_sql({"ref": "orders", "package": "", "version": ""}) == (
        "{{ ref('orders') }}"
    )


def test_un_paquet_ou_une_version_douteuse_est_refuse():
    """Le `ref()` est du Jinja : rien d'arbitraire n'y entre."""
    for inp in (
        {"ref": "orders", "package": "vendor'), ref('autre"},
        {"ref": "orders", "version": "2'); drop"},
    ):
        with pytest.raises(rcp.RecipeError):
            rcp.ref_sql(inp)


def test_le_libelle_d_une_entree_dit_son_paquet():
    assert rcp.input_label({"ref": "orders"}) == "orders"
    assert rcp.input_label({"ref": "orders", "package": "vendor"}) == "orders (vendor)"
    assert rcp.input_label({"ref": "c", "version": "2"}) == "c (v2)"
    assert rcp.input_label({"source_name": "raw", "table": "t"}) == "raw.t"


def test_alias_d_entree():
    assert rcp.input_alias({"alias": "a"}, 0) == "a"
    assert rcp.input_alias({"ref": "stg_orders"}, 0) == "stg_orders"
    assert rcp.input_alias({"table": "orders"}, 0) == "orders"
    assert rcp.input_alias({}, 3) == "input_3"


# ------------------------------------------------------------------ fichier


def test_l_entete_du_fichier_pointe_vers_le_script_visuel(orders: Bench):
    sql = orders.compile(
        prepare_spec(
            [
                {"type": "round", "params": {"column": "amount_eur", "decimals": 0}},
            ]
        )
    )
    assert "Recipe « Préparer » de l'atelier Pliq" in sql
    assert ".pliq/recipes/orders_prepared.yml" in sql
    assert "source de vérité" in sql


def test_le_config_precede_le_bandeau(orders: Bench):
    sql = orders.compile(
        prepare_spec(
            [{"type": "round", "params": {"column": "amount_eur", "decimals": 0}}],
            output={"materialized": "table"},
        )
    )
    assert sql.index("{{ config(") < sql.index("-- Recipe")


def test_chaque_etape_devient_un_cte_commente(orders: Bench):
    sql = orders.compile(
        prepare_spec(
            [
                {
                    "type": "text_transform",
                    "params": {"column": "status", "mode": "upper"},
                },
                {
                    "type": "formula",
                    "params": {"into": "ttc", "expression": "amount_eur * 1.2"},
                },
            ]
        )
    )
    assert "-- 1 · Mettre status en majuscules" in sql
    assert "-- 2 · Calculer ttc = amount_eur * 1.2" in sql
    assert "texte as (" in sql and "formule as (" in sql
    assert sql.rstrip().endswith("select * from formule")


def test_le_sql_produit_est_du_sql_valide(orders: Bench):
    """Ce que l'atelier écrit doit parser, jinja mis à part."""
    sql = orders.compile(
        prepare_spec(
            [
                {
                    "type": "rename",
                    "params": {"renames": [{"from": "status", "to": "etat"}]},
                },
                {
                    "type": "filter_value",
                    "params": {
                        "column": "etat",
                        "operator": "eq",
                        "values": ["completed"],
                        "action": "keep",
                    },
                },
                {"type": "distinct_rows", "params": {}},
                {"type": "sort", "params": {"column": "order_id"}},
            ]
        )
    )
    without_jinja = resolve_refs_for_test(sql, orders.manifest)
    assert sqlglot.parse_one(without_jinja, dialect="duckdb") is not None


# ------------------------------------------------------- deltas de colonnes


def test_les_compteurs_de_colonnes_suivent_le_script(orders: Bench):
    spec = prepare_spec(
        [
            {
                "type": "formula",
                "params": {"into": "ttc", "expression": "amount_eur * 1.2"},
            },
            {
                "type": "keep_delete",
                "params": {"columns": ["country"], "action": "delete"},
            },
            {"type": "text_transform", "params": {"column": "status", "mode": "upper"}},
            {
                "type": "filter_value",
                "params": {
                    "column": "status",
                    "operator": "eq",
                    "values": ["COMPLETED"],
                    "action": "keep",
                },
            },
        ]
    )
    _, _, deltas = rcp.compile_prepare(spec, orders.columns("stg_orders"))

    assert deltas[0]["created"] == ["ttc"]
    assert deltas[1]["deleted"] == ["country"]
    assert deltas[2]["modified"] == ["status"]
    assert deltas[3]["filters"] is True
    assert deltas[0]["filters"] is False
    assert [d["step"] for d in deltas] == ["s0", "s1", "s2", "s3"]
    assert all(d["applied"] for d in deltas)


def _four_steps() -> dict:
    """Un script où chaque étape change les colonnes de la suivante."""
    return prepare_spec(
        [
            {
                "type": "formula",
                "params": {"into": "ttc", "expression": "amount_eur * 1.2"},
            },
            {
                "type": "keep_delete",
                "params": {"columns": ["country"], "action": "delete"},
            },
            {
                "type": "formula",
                "params": {"into": "remise", "expression": "ttc * 0.1"},
            },
            {
                "type": "rename",
                "params": {"renames": [{"from": "remise", "to": "rabais"}]},
            },
        ]
    )


def test_un_apercu_par_etape_decrit_quand_meme_la_suite_du_script(orders: Bench):
    """L'œil borne le SQL exécuté, pas ce qu'on sait des colonnes.

    L'écran ouvre volontiers le formulaire d'une étape placée après l'œil. S'il
    n'a pour toute information que les colonnes de l'aperçu, il propose celles
    de l'œil : « rabais » manque, « country » est offerte alors qu'une étape
    précédente l'a supprimée. Les deltas vont donc jusqu'au bout du script, et
    disent lesquels sont dans le SQL.
    """
    spec = _four_steps()
    sql, _, deltas = rcp.compile_prepare(spec, orders.columns("stg_orders"), upto=2)

    assert [d["applied"] for d in deltas] == [True, True, False, False]
    assert deltas[2]["before"] == deltas[1]["after"]
    assert deltas[2]["created"] == ["remise"]
    assert "country" not in deltas[3]["before"]
    assert "rabais" in deltas[3]["after"]

    # Le SQL, lui, s'arrête bien à l'œil : les deux dernières étapes ne
    # coûtent rien à l'entrepôt.
    assert "remise" not in sql
    assert sql.count(" as (") == 3  # la source, puis les deux étapes exécutées


def test_les_colonnes_finales_ne_dependent_pas_de_l_oeil(orders: Bench):
    cols = orders.columns("stg_orders")
    _, bound_column, _ = rcp.compile_prepare(_four_steps(), cols, upto=1)
    _, as_int, _ = rcp.compile_prepare(_four_steps(), cols)
    assert bound_column.columns == as_int.columns


def test_une_etape_cassee_apres_l_oeil_ne_casse_pas_l_apercu(orders: Bench):
    """C'est justement quand la fin du script est cassée qu'on pose l'œil avant.

    Le suivi des colonnes s'arrête là où il ne sait plus ; l'aperçu, lui, sort
    normalement.
    """
    spec = prepare_spec(
        [
            {
                "type": "keep_delete",
                "params": {"columns": ["country"], "action": "delete"},
            },
            {
                "type": "text_transform",
                "params": {"column": "country", "mode": "upper"},
            },
            {"type": "sort", "params": {"column": "order_id"}},
        ]
    )
    sql, _, deltas = rcp.compile_prepare(spec, orders.columns("stg_orders"), upto=1)

    assert len(deltas) == 1
    assert deltas[0]["applied"] is True
    assert (
        sqlglot.parse_one(resolve_refs_for_test(sql, orders.manifest), dialect="duckdb")
        is not None
    )

    # Sans l'œil, la même étape est bien refusée : on n'a pas rendu le script
    # plus permissif, seulement l'aperçu plus tolérant.
    with pytest.raises(rcp.RecipeError):
        rcp.compile_prepare(spec, orders.columns("stg_orders"))


# ------------------------------------------------------ ce que lit l'écran


def test_chaque_etape_se_raconte_en_une_phrase():
    sentences = [
        ("rename", {"renames": [{"from": "a", "to": "b"}]}, "Renommer a en b"),
        (
            "rename",
            {"renames": [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]},
            "Renommer 2 colonnes",
        ),
        (
            "keep_delete",
            {"columns": ["a", "b"], "action": "keep"},
            "Conserver 2 colonnes",
        ),
        ("formula", {"into": "x", "expression": "1 + 1"}, "Calculer x = 1 + 1"),
        (
            "filter_value",
            {"column": "s", "operator": "eq", "values": ["ok"], "action": "remove"},
            "Retirer les lignes où s vaut ok",
        ),
        ("remove_empty", {"column": "s"}, "Supprimer les lignes où s est vide"),
        ("round", {"column": "a", "decimals": 2}, "Arrondir a à 2 décimale(s)"),
        ("distinct_rows", {}, "Dédoublonner les lignes"),
    ]
    for type_, params, expected in sentences:
        assert rcp.describe_step({"type": type_, "params": params}) == expected


def test_une_etape_sans_phrase_dediee_retombe_sur_son_libelle():
    assert (
        rcp.describe_step({"type": "sort", "params": {"column": "a"}}) == "Trier par a"
    )
    assert rcp.describe_step({"type": "inconnu", "params": {}}) == "inconnu"


def test_la_bibliotheque_est_serialisable_et_complete():
    lib = rcp.library()
    assert len(lib) == len(rcp.PROCESSORS) >= 18
    for proc in lib:
        assert "fn" not in proc, "la fonction Python ne doit pas partir au navigateur"
        assert proc["label"] and proc["category"] and proc["summary"]
    assert {p["category"] for p in lib} >= {
        "Colonnes",
        "Formules",
        "Filtrage",
        "Nettoyage",
        "Nombres",
        "Dates",
        "Lignes",
    }
    assert any(p["shortcut"] for p in lib)


def test_le_type_d_un_modele_ecrit_a_la_main_est_devine():
    assert rcp.infer_type("select * from a left join b on a.id = b.id") == "join"
    assert rcp.infer_type("select count(*) from a group by 1") == "group"
    assert rcp.infer_type("select 1 union all select 2") == "stack"
    assert rcp.infer_type("select * from a") == "sql"


# ------------------------------------------ régressions d'audit (compilation)


def test_une_colonne_a_espace_reste_citee_dans_la_projection(orders: Bench):
    """Une colonne qui traverse une étape sans changer garde ses guillemets.

    Le rendu ré-émettait l'alias nu : « order total » devenait deux
    identifiants, et n'importe quelle étape sur une autre colonne suffisait à
    produire un SQL que l'entrepôt refuse.
    """
    orders.table(
        "ventes",
        '"order total" double, montant double',
        [(10.0, 1.234), (20.0, 5.678)],
    )
    spec = prepare_spec(
        [{"type": "round", "params": {"column": "montant", "decimals": 1}}],
        name="ventes_prep",
        ref="ventes",
    )
    sql = orders.compile(spec)
    assert '"order total"' in sql
    # Et le SQL tourne vraiment, guillemets compris.
    names, lines = orders.run(spec)
    assert names == ["order total", "montant"]
    assert lines == [(10.0, 1.2), (20.0, 5.7)]


def test_une_etape_ne_reprend_pas_le_nom_du_dataset_d_entree(orders: Bench):
    """L'alias d'entrée est un CTE : une étape ne doit pas le redéfinir."""
    orders.table("texte", "a varchar", [("bonjour",)])
    spec = prepare_spec(
        [{"type": "text_transform", "params": {"column": "a", "mode": "upper"}}],
        name="texte_prep",
        ref="texte",
    )
    sql = orders.compile(spec)
    assert sql.count("texte as (") == 1, sql
    assert orders.run(spec)[1] == [("BONJOUR",)]


@pytest.mark.parametrize(
    "spec, expected",
    [
        (
            {
                "name": "j",
                "type": "join",
                "inputs": [
                    {"ref": "jointure", "alias": "jointure"},
                    {"ref": "stg_orders", "alias": "stg_orders"},
                ],
                "joins": [
                    {"type": "inner", "on": [{"left": "k", "right": "order_id"}]}
                ],
            },
            "jointure_2 as (",
        ),
        (
            {
                "name": "g",
                "type": "group",
                "inputs": [{"ref": "agrege", "alias": "agrege"}],
                "group_by": ["k"],
                "aggregations": [{"fn": "count", "column": "*", "alias": "n"}],
            },
            "agrege_2 as (",
        ),
        (
            {
                "name": "e",
                "type": "stack",
                "inputs": [
                    {"ref": "empile", "alias": "empile"},
                    {"ref": "stg_orders", "alias": "stg_orders"},
                ],
            },
            "empile_2 as (",
        ),
    ],
)
def test_les_autres_compilateurs_evitent_aussi_la_collision(
    orders: Bench, spec: dict, expected: str
):
    for name in ("jointure", "agrege", "empile"):
        orders.table(name, "k integer", [(1,)])
    sql = rcp.COMPILERS[spec["type"]](
        spec, orders.columns(*[i["alias"] for i in spec["inputs"]])
    )
    assert expected in sql, sql


def test_un_incremental_vide_peut_encore_se_remplir():
    """`max()` vaut NULL sur une table vide : le filtre doit le prévoir.

    Sans le test `is null`, la comparaison à NULL ne laisse jamais passer une
    ligne — un incrémental parti vide le reste indéfiniment.
    """
    block = rcp.incremental_filter(
        {"materialized": "incremental", "incremental": {"column": "updated_at"}}
    )
    assert "is null" in block
    assert "or updated_at > (select max(updated_at) from {{ this }})" in block


def test_un_incremental_sans_colonne_ne_filtre_rien():
    assert rcp.incremental_filter({"materialized": "incremental"}) == ""
    assert rcp.incremental_filter({"materialized": "table"}) == ""


# ----------------------------------------------- chemin d'écriture du modèle


def test_le_chemin_du_modele_garde_les_model_paths_imbriques():
    """`model-paths: ["transform/models"]` ne se réduit pas à `models`."""
    assert rcp.model_path({"name": "ventes"}, "transform/models") == (
        "transform/models/ventes.sql"
    )
    assert rcp.model_path(
        {"name": "ventes", "output": {"layer": "marts"}}, "t/models"
    ) == ("t/models/marts/ventes.sql")


# ------------------------------------------------------- contrôle complet


def test_les_mesures_du_controle_complet_couvrent_chaque_colonne():
    measures, plan, discarded = full_check.profile_measures(
        [{"name": "id", "type": "INTEGER"}, {"name": "nom", "type": "VARCHAR"}]
    )
    assert "count(*) as n_lignes" in measures
    for p in ("c0", "c1"):
        for what in ("remplies", "distinctes", "min", "max"):
            assert f"{p}_{what}" in measures
    assert [c["name"] for c in plan] == ["id", "nom"]
    assert discarded == {"type": [], "limite": [], "mesures": {}}


def test_une_colonne_de_type_inconnu_est_laissee_de_cote():
    """`min` sur une structure fait échouer la requête, et on perd tout le reste."""
    _, plan, discarded = full_check.profile_measures(
        [{"name": "ok", "type": "INTEGER"}, {"name": "bizarre", "type": None}]
    )
    assert [c["name"] for c in plan] == ["ok"]
    assert discarded["type"] == ["bizarre"] and discarded["limite"] == []


def test_une_table_large_dit_qu_elle_est_large_et_pas_qu_elle_est_bizarre():
    """Deux raisons d'écarter une colonne : les confondre annoncerait un type
    incompréhensible là où il n'y a qu'une table de soixante-dix colonnes."""
    columns = [{"name": f"c{i}", "type": "INTEGER"} for i in range(70)]
    _, plan, discarded = full_check.profile_measures(columns)
    assert len(plan) == full_check.PROFILE_MAX_COLUMNS
    assert discarded["type"] == []
    assert len(discarded["limite"]) == 70 - full_check.PROFILE_MAX_COLUMNS


def test_chaque_compilateur_finit_par_la_ligne_que_l_agregation_reconnait():
    """Sinon le contrôle complet retombe sans bruit sur la sous-requête.

    Et cette sous-requête, Redshift la refuse quand le SQL contient un `with` —
    c'est-à-dire toujours, pour un modèle produit par l'atelier.
    """
    cols = {
        "a": [{"name": "id", "type": "INTEGER"}, {"name": "v", "type": "DOUBLE"}],
        "b": [{"name": "id", "type": "INTEGER"}],
    }
    specs = {
        "prepare": {
            "type": "prepare",
            "inputs": [{"ref": "a", "alias": "a"}],
            "steps": [],
        },
        "join": {
            "type": "join",
            "inputs": [{"ref": "a", "alias": "a"}, {"ref": "b", "alias": "b"}],
            "joins": [{"type": "left", "on": [{"left": "id", "right": "id"}]}],
            "select": [{"from": "a", "column": "id"}],
        },
        "group": {
            "type": "group",
            "inputs": [{"ref": "a", "alias": "a"}],
            "group_by": ["id"],
            "aggregations": [{"fn": "sum", "column": "v", "alias": "total"}],
        },
        "stack": {
            "type": "stack",
            "inputs": [{"ref": "a", "alias": "a"}, {"ref": "b", "alias": "b"}],
            "mode": "all",
        },
    }
    for name, spec in specs.items():
        sql = rcp.compile_recipe({"name": "x", "output": {}, **spec}, cols)
        assert rcp.FINAL_SELECT.search(sql), f"{name} : {sql[-80:]!r}"
        aggregated = full_check.aggregate_over(sql, "count(*) as n_lignes")
        assert "as source" not in aggregated, f"{name} est retombé sur la sous-requête"


def test_l_agregation_se_pose_sur_le_dernier_cte():
    """Pas de sous-requête quand on peut l'éviter : Redshift refuse un `with` dedans."""
    sql = "with a as (\n\n    select 1\n\n)\n\nselect * from a\n"
    out = full_check.aggregate_over(sql, "count(*) as n_lignes")
    assert out.startswith("with a as (")
    assert out.rstrip().endswith("from a")
    assert "select * from a" not in out


def test_un_sql_ecrit_a_la_main_passe_par_une_sous_requete():
    out = full_check.aggregate_over("select 1 as x\n", "count(*) as n_lignes")
    assert "from (\nselect 1 as x\n) as source" in out


def test_le_controle_complet_se_relit_en_profil():
    plan = [{"name": "id", "prefix": "c0", "type": "INTEGER"}]
    profile_data = full_check.read_profile(
        plan,
        {
            "n_lignes": 10,
            "c0_remplies": 7,
            "c0_distinctes": 7,
            "c0_min": "1",
            "c0_max": "9",
        },
    )
    assert profile_data["total"] == 10
    assert profile_data["columns"][0] == {
        "name": "id",
        "type": "INTEGER",
        "filled": 7,
        "empty": 3,
        "distinct": 7,
        "min": "1",
        "max": "9",
        "omitted": [],
    }


def test_l_avant_apres_dit_ce_qui_a_change():
    """Les deux phrases que l'échantillon ne peut pas produire."""
    before = {"total": 1000, "columns": [{"name": "d", "empty": 10}]}
    after = {"total": 880, "columns": [{"name": "d", "empty": 10}]}
    lines = full_check.profile_delta(before, after)
    assert lines == ["120 lignes supprimées (12.0 %) — 1000 en entrée, 880 en sortie."]

    same_profile = {"total": 1000, "columns": [{"name": "d", "empty": 350}]}
    assert full_check.profile_delta(before, same_profile) == [
        "340 valeurs de « d » sont devenues vides."
    ]


def test_les_vides_ne_se_comparent_pas_a_nombre_de_lignes_different():
    """Un filtre fait perdre des vides sans qu'aucune valeur n'ait été abîmée."""
    before = {"total": 100, "columns": [{"name": "d", "empty": 0}]}
    after = {"total": 40, "columns": [{"name": "d", "empty": 30}]}
    assert not any(
        "devenues vides" in p for p in full_check.profile_delta(before, after)
    )


# ------------------- un nom que dbt porte déjà n'est pas un nom de modèle


@pytest.mark.parametrize(
    "table",
    ["order-items", "2024_sales", "Clients Été", "order.items", "CAMEL-case"],
)
def test_une_source_se_designe_sous_le_nom_qu_elle_porte(table: str):
    """`ref_sql` appliquait aux sources la règle d'un identifiant de modèle.

    La déclaration de `order-items` réussissait, `dbt source()` la lisait très
    bien, et l'aperçu de l'atelier refusait son nom. Un nom de table appartient
    à l'entrepôt : on ne le réécrit pas, on le cite.
    """
    assert rcp.ref_sql({"source_name": "brut", "table": table}) == (
        f"{{{{ source('brut', '{table}') }}}}"
    )


def test_un_nom_de_modele_cree_par_l_atelier_reste_sage():
    """La souplesse vaut pour ce qu'on désigne, pas pour ce qu'on écrit."""
    with pytest.raises(rcp.RecipeError, match="Nom de modèle invalide"):
        rcp.validate_name("order-items")


def test_une_apostrophe_est_echappee_et_ne_ferme_pas_la_chaine():
    """Ce qui gardait le Jinja sain, c'était la règle des identifiants.

    En l'assouplissant, c'est l'échappement qui prend le relais — sinon un nom
    portant une apostrophe fermerait la chaîne et ferait passer la suite pour
    de l'expression.
    """
    assert rcp.jinja_str("l'été") == "'l\\'été'"
    assert rcp.jinja_str("a\\b") == "'a\\\\b'"
    output = rcp.ref_sql({"source_name": "brut", "table": "d'hier"})
    assert output == "{{ source('brut', 'd\\'hier') }}"

    rendered = (
        Environment()
        .from_string("{% macro source(a, b) %}{{ a }}|{{ b }}{% endmacro %}" + output)
        .render()
    )
    assert rendered.strip() == "brut|d'hier", "Jinja doit relire exactement le nom"


@pytest.mark.parametrize("wrong", ["", "   ", "a\nb", "x\ty"])
def test_ce_qui_n_est_pas_un_nom_dbt_reste_refuse(wrong: str):
    with pytest.raises(rcp.RecipeError):
        rcp.dbt_name(wrong, "table")


def test_l_assouplissement_ne_vaut_que_pour_les_sources():
    """Un paquet et une version sont des noms que dbt déclare, pas l'entrepôt.

    Le constat portait sur les tables de sources, qui portent le nom qu'elles
    ont dans l'entrepôt. Rien n'oblige à relâcher le reste, et la règle des
    identifiants y reste le premier garde-fou.
    """
    with pytest.raises(rcp.RecipeError):
        rcp.ref_sql({"ref": "orders", "package": "vendor-x"})
    with pytest.raises(rcp.RecipeError):
        rcp.ref_sql({"ref": "order-items"})

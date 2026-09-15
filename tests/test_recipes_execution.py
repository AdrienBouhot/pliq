"""Le SQL produit par une recipe est exécuté pour de vrai sur DuckDB.

On ne compare pas des chaînes de caractères : on regarde les lignes qui sortent.
C'est ce qui distingue « le compilateur a écrit quelque chose » de « la recipe
fait ce qu'elle annonce ».
"""

from __future__ import annotations

import pytest

from pliq import profiling
from pliq import join_diagnostics
from pliq import recipes as rcp

from .conftest import Bench, prepare_spec, resolve_refs_for_test

# --------------------------------------------------------------- Colonnes


def test_renommer(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "rename",
                    "params": {"renames": [{"from": "amount_eur", "to": "montant"}]},
                },
            ]
        )
    )
    assert "montant" in cols and "amount_eur" not in cols
    assert rows[0][cols.index("montant")] == pytest.approx(100.456)


def test_conserver_puis_supprimer_des_colonnes(orders: Bench):
    cols, _ = orders.run(
        prepare_spec(
            [
                {
                    "type": "keep_delete",
                    "params": {
                        "columns": ["order_id", "status", "amount_eur"],
                        "action": "keep",
                    },
                },
                {
                    "type": "keep_delete",
                    "params": {"columns": ["status"], "action": "delete"},
                },
            ]
        )
    )
    assert cols == ["order_id", "amount_eur"]


def test_concatener_ignore_les_nulls(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "concat_columns",
                    "params": {
                        "columns": ["order_id", "status"],
                        "into": "etiquette",
                        "separator": " - ",
                    },
                },
            ]
        )
    )
    i = cols.index("etiquette")
    assert rows[0][i] == "1 - completed"
    assert rows[3][i] == "4 - "  # status null → chaîne vide, pas null


def test_decouper_une_colonne(bench: Bench):
    bench.table("contacts", "nom varchar", [("dupont,jean",), ("martin,claire",)])
    cols, rows = bench.run(
        prepare_spec(
            [
                {
                    "type": "split_column",
                    "params": {"column": "nom", "separator": ",", "count": 2},
                },
            ],
            ref="contacts",
        )
    )
    assert cols == ["nom", "nom_1", "nom_2"]
    assert rows[0][1:] == ("dupont", "jean")


# ---------------------------------------------------------------- Formules


def test_formule_cree_une_colonne(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "formula",
                    "params": {
                        "into": "amount_ttc",
                        "expression": "round(amount_eur * 1.2, 2)",
                    },
                },
            ]
        )
    )
    assert rows[0][cols.index("amount_ttc")] == pytest.approx(120.55)


def test_formule_ecrase_une_colonne_existante_sans_la_dupliquer(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "formula",
                    "params": {"into": "amount_eur", "expression": "amount_eur * 2"},
                },
            ]
        )
    )
    assert cols.count("amount_eur") == 1
    assert rows[0][cols.index("amount_eur")] == pytest.approx(200.912)


def test_si_alors_sinon(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "if_then_else",
                    "params": {
                        "into": "gros_panier",
                        "condition": "amount_eur > 50",
                        "then": "'oui'",
                        "otherwise": "'non'",
                    },
                },
            ]
        )
    )
    i = cols.index("gros_panier")
    assert [r[i] for r in rows] == ["oui", "non", "non", "non", "non"]


# --------------------------------------------------------------- Filtrage


@pytest.mark.parametrize(
    "params, expected",
    [
        (
            {
                "column": "status",
                "operator": "eq",
                "values": ["completed"],
                "action": "keep",
            },
            [1, 3],
        ),
        (
            {
                "column": "status",
                "operator": "in",
                "values": ["completed", "PENDING"],
                "action": "keep",
            },
            [1, 2, 3],
        ),
        (
            {
                "column": "status",
                "operator": "not_in",
                "values": ["completed"],
                "action": "keep",
            },
            [2],
        ),
        ({"column": "status", "operator": "empty", "action": "keep"}, [4, 4]),
        ({"column": "status", "operator": "not_empty", "action": "keep"}, [1, 2, 3]),
        (
            {
                "column": "status",
                "operator": "contains",
                "values": ["mplet"],
                "action": "keep",
            },
            [1, 3],
        ),
        (
            {
                "column": "status",
                "operator": "starts_with",
                "values": ["comp"],
                "action": "keep",
            },
            [1, 3],
        ),
        (
            {
                "column": "status",
                "operator": "ends_with",
                "values": ["ING"],
                "action": "keep",
            },
            [2],
        ),
        (
            {
                "column": "amount_eur",
                "operator": "gt",
                "values": [10],
                "action": "keep",
            },
            [1, 2],
        ),
        (
            {
                "column": "amount_eur",
                "operator": "lte",
                "values": [20],
                "action": "keep",
            },
            [2, 3],
        ),
    ],
)
def test_filtrer_sur_une_valeur(orders: Bench, params: dict, expected: list[int]):
    cols, rows = orders.run(prepare_spec([{"type": "filter_value", "params": params}]))
    assert sorted(r[cols.index("order_id")] for r in rows) == sorted(expected)


@pytest.mark.parametrize(
    ("op", "looks_up", "expected"),
    [
        # `%` et `_` sont les jokers de `like`. L'écran propose une recherche de
        # valeur : ils doivent se chercher comme le reste, à la lettre.
        ("contains", "%", ["50%"]),
        ("contains", "_", ["a_b"]),
        ("contains", "50%", ["50%"]),
        ("contains", "a_b", ["a_b"]),
        # Le caractère d'échappement se cherche aussi lui-même.
        ("contains", "!", ["a!b"]),
        ("contains", "\\", ["a\\b"]),
        ("starts_with", "a_", ["a_b"]),
        ("ends_with", "_b", ["a_b"]),
        # Ce qui ressemblait à un joker mais n'en est pas continue de marcher.
        ("contains", "ab", ["abc"]),
    ],
)
def test_chercher_du_texte_ne_lit_pas_de_motif_sql(
    bench: Bench, op: str, looks_up: str, expected: list[str]
):
    """« Contient 50% » gardait les quatre lignes : le motif valait « tout ».

    Le même défaut retirait des lignes sans le caractère cherché quand l'action
    était « retirer » — un filtre qui supprime ce qu'on ne lui a pas désigné.
    """
    bench.table(
        "textes",
        "value varchar",
        [("50%",), ("abc",), ("a_b",), ("axb",), ("a!b",), ("a\\b",)],
    )
    spec = prepare_spec(
        [
            {
                "type": "filter_value",
                "params": {
                    "column": "value",
                    "operator": op,
                    "values": [looks_up],
                    "action": "keep",
                },
            }
        ],
        ref="textes",
    )
    cols, rows = bench.run(spec)
    assert sorted(r[cols.index("value")] for r in rows) == sorted(expected)


def test_retirer_ne_supprime_que_ce_qui_est_cherche(bench: Bench):
    """L'autre moitié du défaut : « retirer % » vidait la table entière."""
    bench.table("textes", "value varchar", [("50%",), ("abc",), ("a_b",)])
    cols, rows = bench.run(
        prepare_spec(
            [
                {
                    "type": "filter_value",
                    "params": {
                        "column": "value",
                        "operator": "contains",
                        "values": ["%"],
                        "action": "remove",
                    },
                }
            ],
            ref="textes",
        )
    )
    assert sorted(r[cols.index("value")] for r in rows) == ["a_b", "abc"]


@pytest.mark.parametrize("op", ["contains", "starts_with", "ends_with"])
def test_chercher_sans_valeur_est_refuse(orders: Bench, op: str):
    """Sans garde, `values[0]` levait une IndexError — donc un 500 muet."""
    with pytest.raises(rcp.RecipeError, match="aucune valeur"):
        orders.compile(
            prepare_spec(
                [
                    {
                        "type": "filter_value",
                        "params": {
                            "column": "status",
                            "operator": op,
                            "values": [],
                            "action": "keep",
                        },
                    }
                ]
            )
        )


def test_filtrer_compare_un_nombre_sans_le_quoter(orders: Bench):
    """Une valeur saisie en texte sur une colonne numérique doit rester un nombre."""
    sql = orders.compile(
        prepare_spec(
            [
                {
                    "type": "filter_value",
                    "params": {
                        "column": "amount_eur",
                        "operator": "gt",
                        "values": ["10"],
                        "action": "keep",
                    },
                },
            ]
        )
    )
    assert "amount_eur > 10" in sql
    _cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "filter_value",
                    "params": {
                        "column": "amount_eur",
                        "operator": "gt",
                        "values": ["10"],
                        "action": "keep",
                    },
                },
            ]
        )
    )
    assert len(rows) == 2


def test_retirer_des_lignes_ne_doit_pas_emporter_les_lignes_vides(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "filter_value",
                    "params": {
                        "column": "status",
                        "operator": "eq",
                        "values": ["completed"],
                        "action": "remove",
                    },
                },
            ]
        )
    )
    assert sorted(r[cols.index("order_id")] for r in rows) == [2, 4, 4]


def test_vider_une_valeur_garde_la_ligne(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "filter_value",
                    "params": {
                        "column": "status",
                        "operator": "eq",
                        "values": ["PENDING"],
                        "action": "clear",
                    },
                },
            ]
        )
    )
    i = cols.index("status")
    assert len(rows) == 5
    assert rows[1][i] is None


def test_filtrer_avec_une_formule(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "filter_formula",
                    "params": {"condition": "customer_id = 10", "action": "remove"},
                },
            ]
        )
    )
    assert {r[cols.index("customer_id")] for r in rows} == {11, 12}


def test_supprimer_les_lignes_vides(orders: Bench):
    _cols, rows = orders.run(
        prepare_spec(
            [
                {"type": "remove_empty", "params": {"column": "country"}},
            ]
        )
    )
    assert len(rows) == 4  # la ligne 3 (country null) part


# -------------------------------------------------------------- Nettoyage


def test_remplir_les_cellules_vides_texte_et_nombre(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "fill_empty",
                    "params": {"column": "status", "value": "inconnu"},
                },
                {
                    "type": "fill_empty",
                    "params": {"column": "amount_eur", "value": "0"},
                },
            ]
        )
    )
    assert rows[3][cols.index("status")] == "inconnu"
    assert rows[3][cols.index("amount_eur")] == 0.0


def test_une_cellule_vide_veut_dire_la_meme_chose_partout(bench: Bench):
    """Le profilage, « Remplir » et « Supprimer » comptaient trois choses.

    Le profilage classe vide ce qui est `null`, `''` ou une chaîne d'espaces —
    et c'est ce compte qui fait apparaître les deux transformations à côté de la
    colonne. « Remplir » n'appliquait pourtant qu'un `coalesce()`, donc ne voyait
    que les `null` : la suggestion s'appliquait, et le compteur de vides ne
    bougeait pas. « Supprimer » attrapait `''` mais laissait passer `'   '`.
    """
    bench.table(
        "clients",
        "id integer, nom varchar",
        [(1, "ada"), (2, None), (3, ""), (4, "   "), (5, "bob")],
    )
    spec = prepare_spec(
        [{"type": "fill_empty", "params": {"column": "nom", "value": "inconnu"}}],
        ref="clients",
    )
    cols, rows = bench.run(spec)
    assert [r[cols.index("nom")] for r in rows] == [
        "ada",
        "inconnu",
        "inconnu",
        "inconnu",
        "bob",
    ]

    cols, rows = bench.run(
        prepare_spec(
            [{"type": "remove_empty", "params": {"column": "nom"}}], ref="clients"
        )
    )
    assert [r[cols.index("id")] for r in rows] == [1, 5]

    # Les trois mêmes cellules que celles que le profilage compte : c'est son
    # `n_empty` qui propose ces deux étapes, elles doivent donc les traiter.
    profile_data = profiling.profile(
        [{"name": "nom", "type": "VARCHAR"}],
        [["ada"], [None], [""], ["   "], ["bob"]],
    )
    assert profile_data[0]["n_empty"] == 3


def test_remplir_une_colonne_numerique_ne_teste_pas_la_chaine_vide(bench: Bench):
    """La chaîne vide n'existe pas sur un nombre : `coalesce` dit tout, et se lit."""
    bench.table("mesures", "id integer, n double", [(1, 1.5), (2, None)])
    spec = prepare_spec(
        [{"type": "fill_empty", "params": {"column": "n", "value": "0"}}], ref="mesures"
    )
    assert "coalesce(n, 0)" in bench.compile(spec)
    cols, rows = bench.run(spec)
    assert [r[cols.index("n")] for r in rows] == [1.5, 0.0]


def test_rechercher_remplacer(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "find_replace",
                    "params": {
                        "column": "status",
                        "find": "completed",
                        "replace": "terminé",
                    },
                },
            ]
        )
    )
    assert rows[0][cols.index("status")] == "terminé"


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("upper", "FR"),
        ("lower", "fr"),
        ("trim", "fr"),
        ("trim_lower", "fr"),
        ("capitalize", "Fr"),
    ],
)
def test_transformer_le_texte(orders: Bench, mode: str, expected: str):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "text_transform",
                    "params": {"column": "country", "mode": mode},
                },
            ]
        )
    )
    assert rows[0][cols.index("country")] == expected


def test_changer_le_type(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "change_type",
                    "params": {"column": "amount_eur", "to": "integer"},
                },
            ]
        )
    )
    assert rows[0][cols.index("amount_eur")] == 100


def test_arrondir(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {"type": "round", "params": {"column": "amount_eur", "decimals": 2}},
            ]
        )
    )
    assert rows[0][cols.index("amount_eur")] == pytest.approx(100.46)


# ------------------------------------------------------------------ Dates


def test_parser_une_date_avec_format(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "parse_date",
                    "params": {
                        "column": "ordered_at",
                        "format": "%d/%m/%Y",
                        "into": "date_commande",
                    },
                },
            ]
        )
    )
    assert str(rows[0][cols.index("date_commande")]) == "2024-02-01 00:00:00"


def test_parser_une_date_sans_format_ne_casse_pas_sur_les_valeurs_illisibles(
    bench: Bench,
):
    bench.table("evts", "d varchar", [("2024-02-01",), ("pas une date",)])
    _cols, rows = bench.run(
        prepare_spec(
            [
                {"type": "parse_date", "params": {"column": "d", "format": ""}},
            ],
            ref="evts",
        )
    )
    assert str(rows[0][0]) == "2024-02-01"
    assert rows[1][0] is None  # try_cast, pas cast


def test_extraire_les_composants_de_date(bench: Bench):
    bench.table("evts", "d date", [("2024-02-29",)])
    cols, rows = bench.run(
        prepare_spec(
            [
                {
                    "type": "extract_date_parts",
                    "params": {
                        "column": "d",
                        "parts": ["year", "month", "day", "quarter", "dow"],
                    },
                },
            ],
            ref="evts",
        )
    )
    assert cols == ["d", "d_year", "d_month", "d_day", "d_quarter", "d_dow"]
    assert rows[0][1:4] == (2024, 2, 29)


# ----------------------------------------------------------------- Lignes


def test_dedoublonner(orders: Bench):
    _, rows = orders.run(prepare_spec([{"type": "distinct_rows", "params": {}}]))
    assert len(rows) == 4


def test_trier(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {"type": "sort", "params": {"column": "order_id", "descending": True}},
            ]
        )
    )
    assert [r[cols.index("order_id")] for r in rows] == [4, 4, 3, 2, 1]


# ------------------------------------------------------------- le script


def test_les_etapes_s_enchainent_dans_l_ordre(orders: Bench):
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "text_transform",
                    "params": {"column": "status", "mode": "lower"},
                },
                {
                    "type": "filter_value",
                    "params": {
                        "column": "status",
                        "operator": "eq",
                        "values": ["pending"],
                        "action": "keep",
                    },
                },
            ]
        )
    )
    assert len(rows) == 1 and rows[0][cols.index("order_id")] == 2


def test_une_etape_desactivee_est_ignoree(orders: Bench):
    spec = prepare_spec(
        [
            {
                "type": "filter_value",
                "params": {
                    "column": "status",
                    "operator": "eq",
                    "values": ["completed"],
                    "action": "keep",
                },
            },
        ]
    )
    spec["steps"][0]["enabled"] = False
    _, rows = orders.run(spec)
    assert len(rows) == 5


def test_apercu_a_une_etape_donnee(orders: Bench):
    """L'œil d'une étape montre l'état du flux *à cette étape*, pas à la fin."""
    spec = prepare_spec(
        [
            {
                "type": "filter_value",
                "params": {
                    "column": "status",
                    "operator": "eq",
                    "values": ["completed"],
                    "action": "keep",
                },
            },
            {
                "type": "keep_delete",
                "params": {"columns": ["order_id"], "action": "keep"},
            },
        ]
    )
    cols_1, rows_1 = orders.run(spec, upto=1)
    cols_2, _ = orders.run(spec, upto=2)
    assert len(rows_1) == 2 and len(cols_1) == 6
    assert cols_2 == ["order_id"]


def test_deux_etapes_du_meme_type_ne_collisionnent_pas(orders: Bench):
    sql = orders.compile(
        prepare_spec(
            [
                {"type": "round", "params": {"column": "amount_eur", "decimals": 1}},
                {"type": "round", "params": {"column": "amount_eur", "decimals": 0}},
            ]
        )
    )
    assert "arrondi as (" in sql and "arrondi_2 as (" in sql
    _, rows = orders.run(
        prepare_spec(
            [
                {"type": "round", "params": {"column": "amount_eur", "decimals": 1}},
                {"type": "round", "params": {"column": "amount_eur", "decimals": 0}},
            ]
        )
    )
    assert rows[0][3] == pytest.approx(101.0)  # round(round(100.456, 1), 0)


def test_une_recipe_sans_etape_recopie_l_entree(orders: Bench):
    cols, rows = orders.run(prepare_spec([]))
    assert len(rows) == 5 and len(cols) == 6


# ------------------------------------------------------------------ Join


def _join_spec(select: list[dict] | None = None, jtype: str = "left") -> dict:
    return {
        "name": "commandes_clients",
        "type": "join",
        "inputs": [
            {"ref": "stg_orders", "alias": "stg_orders"},
            {"ref": "stg_customers", "alias": "stg_customers"},
        ],
        "joins": [
            {"type": jtype, "on": [{"left": "customer_id", "right": "customer_id"}]}
        ],
        "select": select or [],
        "output": {"materialized": "table"},
    }


@pytest.fixture
def join_spec(orders: Bench) -> Bench:
    orders.table(
        "stg_customers",
        "customer_id integer, nom varchar",
        [(10, "Dupont"), (11, "Martin")],
    )
    return orders


def test_jointure_gauche_garde_les_orphelins(join_spec: Bench):
    cols, rows = join_spec.run(_join_spec())
    assert len(rows) == 5
    assert rows[3][cols.index("nom")] is None  # client 12 absent


def test_jointure_interne_les_retire(join_spec: Bench):
    _, rows = join_spec.run(_join_spec(jtype="inner"))
    assert len(rows) == 3


def test_jointure_croisee(join_spec: Bench):
    spec = _join_spec()
    spec["joins"] = [{"type": "cross"}]
    _, rows = join_spec.run(spec)
    assert len(rows) == 10


def test_jointure_avec_selection_et_renommage(join_spec: Bench):
    cols, rows = join_spec.run(
        _join_spec(
            [
                {"from": "stg_orders", "column": "order_id"},
                {"from": "stg_orders", "column": "customer_id"},
                {
                    "from": "stg_customers",
                    "column": "customer_id",
                    "as": "customer_id_client",
                },
                {"from": "stg_customers", "column": "nom", "as": "client"},
            ]
        )
    )
    assert cols == ["order_id", "customer_id", "customer_id_client", "client"]
    assert rows[0][cols.index("client")] == "Dupont"


def test_jointure_sur_plusieurs_cles(join_spec: Bench):
    spec = _join_spec()
    spec["joins"][0]["on"] = [
        {"left": "customer_id", "right": "customer_id"},
        {"left": "customer_id", "right": "customer_id"},
    ]
    sql = join_spec.compile(spec)
    assert sql.count("stg_orders.customer_id = stg_customers.customer_id") == 2
    _, rows = join_spec.run(spec)
    assert len(rows) == 5


# ----------------------------------------------------------------- Group


def test_grouper_avec_cles_et_mesures(orders: Bench):
    cols, rows = orders.run(
        {
            "name": "par_client",
            "type": "group",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "group_by": ["customer_id"],
            "aggregations": [
                {"fn": "count", "column": "*", "alias": "nb_commandes"},
                {"fn": "sum", "column": "amount_eur", "alias": "total_eur"},
                {"fn": "count_distinct", "column": "status", "alias": "nb_statuts"},
            ],
            "output": {"materialized": "table"},
        }
    )
    by_customer = {r[cols.index("customer_id")]: r for r in rows}
    assert by_customer[10][cols.index("nb_commandes")] == 2
    assert by_customer[10][cols.index("total_eur")] == pytest.approx(120.456)
    assert by_customer[12][cols.index("nb_statuts")] == 0


def test_mesure_avec_condition(orders: Bench):
    _cols, rows = orders.run(
        {
            "name": "par_client",
            "type": "group",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "group_by": ["customer_id"],
            "aggregations": [
                {
                    "fn": "count",
                    "column": "*",
                    "alias": "nb_terminees",
                    "filter": "status = 'completed'",
                }
            ],
            "output": {},
        }
    )
    by_customer = {r[0]: r[1] for r in rows}
    assert by_customer[10] == 1 and by_customer[12] == 0


def test_grouper_sans_cle_donne_une_seule_ligne(orders: Bench):
    _cols, rows = orders.run(
        {
            "name": "total",
            "type": "group",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "group_by": [],
            "aggregations": [{"fn": "max", "column": "amount_eur", "alias": "max_eur"}],
            "output": {},
        }
    )
    assert rows == [(pytest.approx(100.456),)]


# ----------------------------------------------------------------- Stack


def test_empiler_aligne_par_nom_et_complete_par_null(bench: Bench):
    bench.table("fr", "id integer, pays varchar", [(1, "FR")])
    bench.table("es", "id integer, ville varchar", [(2, "Madrid")])
    spec = {
        "name": "tout",
        "type": "stack",
        "inputs": [{"ref": "fr", "alias": "fr"}, {"ref": "es", "alias": "es"}],
        "mode": "all",
        "output": {},
    }
    cols, rows = bench.run(spec)
    assert cols == ["id", "pays", "ville"]
    assert sorted(rows) == [(1, "FR", None), (2, None, "Madrid")]


def test_empiler_en_dedoublonnant(bench: Bench):
    bench.table("a", "id integer", [(1,), (2,)])
    bench.table("b", "id integer", [(2,), (3,)])
    spec = {
        "name": "tout",
        "type": "stack",
        "inputs": [{"ref": "a", "alias": "a"}, {"ref": "b", "alias": "b"}],
        "mode": "distinct",
        "output": {},
    }
    _, rows = bench.run(spec)
    assert sorted(r[0] for r in rows) == [1, 2, 3]


# ------------------------------------------------------------------- SQL


def test_recipe_sql_est_recopiee_telle_quelle(bench: Bench):
    bench.table("stg_orders", "order_id integer", [(1,)])
    cols, rows = bench.run(
        {
            "name": "brut",
            "type": "sql",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "sql": "select order_id * 10 as dix from {{ ref('stg_orders') }}",
            "output": {},
        }
    )
    assert cols == ["dix"] and rows == [(10,)]


# -------------------------------------------------------------- sources


def test_une_recipe_peut_partir_d_une_source(bench: Bench):
    bench.source_table(
        "raw",
        "raw_orders",
        "order_id integer, status varchar",
        [(1, "completed"), (2, "pending")],
    )
    spec = prepare_spec(
        [{"type": "text_transform", "params": {"column": "status", "mode": "upper"}}],
        ref="raw_orders",
    )
    spec["inputs"] = [
        {"source_name": "raw", "table": "raw_orders", "alias": "raw_orders"}
    ]
    sql = bench.compile(spec)
    assert "{{ source('raw', 'raw_orders') }}" in sql
    cols, rows = bench.run(spec)
    assert rows[0][cols.index("status")] == "COMPLETED"


# --------------------------------------------------------- incrémental


def test_l_apercu_d_un_incremental_montre_le_rafraichissement_complet(orders: Bench):
    """Le bloc is_incremental() est retiré à l'aperçu, comme au premier build."""
    spec = prepare_spec(
        [{"type": "round", "params": {"column": "amount_eur", "decimals": 0}}],
        output={
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "unique_key": ["order_id"],
            "incremental": {"column": "ordered_at", "operator": "gt"},
        },
    )
    sql = orders.compile(spec)
    assert "{% if is_incremental() %}" in sql
    _, rows = orders.run(spec)
    assert len(rows) == 5


def test_un_deuxieme_build_incremental_n_ajoute_que_le_nouveau(bench: Bench):
    """Le cas nominal : deux builds, et seule la ligne neuve entre."""
    bench.table(
        "ventes",
        "id integer, ts varchar, ville varchar",
        [(1, "2024-01-01", " lyon "), (2, "2024-01-02", "nice")],
    )
    # L'étape porte sur une colonne quelconque, pas sur `ts` : transformer la
    # colonne de borne est refusé, et c'est l'objet d'un autre test.
    spec = prepare_spec(
        [{"type": "text_transform", "params": {"column": "ville", "mode": "trim"}}],
        ref="ventes",
        output={
            "materialized": "incremental",
            "incremental_strategy": "append",
            "incremental": {"column": "ts", "operator": "gt"},
        },
    )
    _, rows = bench.build(spec)
    assert len(rows) == 2

    _, rows = bench.build(spec)
    assert len(rows) == 2, "un build sans nouveauté ne doit rien réempiler"

    bench.con.execute("insert into main.ventes values (3, '2024-01-03', 'caen')")
    _, rows = bench.build(spec)
    assert len(rows) == 3


def test_un_empilement_incremental_ne_duplique_pas_la_deuxieme_entree(bench: Bench):
    """Seule la première entrée recevait le filtre : la seconde se réempilait."""
    bench.table("ventes_fr", "id integer, ts varchar", [(1, "2024-01-01")])
    bench.table("ventes_es", "id integer, ts varchar", [(2, "2024-01-02")])
    spec = {
        "name": "ventes",
        "type": "stack",
        "inputs": [{"ref": "ventes_fr"}, {"ref": "ventes_es"}],
        "mode": "all",
        "output": {
            "materialized": "incremental",
            "incremental_strategy": "append",
            "incremental": {"column": "ts", "operator": "gt"},
        },
    }
    _, rows = bench.build(spec)
    assert len(rows) == 2

    _, rows = bench.build(spec)
    assert len(rows) == 2, "aucune source n'a bougé : la table ne doit pas grossir"


def test_une_agregation_incrementale_garde_l_historique(bench: Bench):
    """Filtrer avant de regrouper remplaçait le total par celui du jour."""
    bench.table(
        "lignes",
        "client varchar, montant integer, ts varchar",
        [("a", 10, "2024-01-01")],
    )
    spec = {
        "name": "totaux",
        "type": "group",
        "inputs": [{"ref": "lignes"}],
        "group_by": ["client"],
        "aggregations": [
            {"fn": "sum", "column": "montant", "alias": "total"},
            {"fn": "max", "column": "ts", "alias": "ts"},
        ],
        "output": {
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "unique_key": ["client"],
            "incremental": {"column": "ts", "operator": "gt"},
        },
    }
    cols, rows = bench.build(spec)
    assert rows == [("a", 10, "2024-01-01")]

    bench.con.execute("insert into main.lignes values ('a', 5, '2024-01-02')")
    cols, rows = bench.build(spec)
    total = rows[0][cols.index("total")]
    assert total == 15, "le groupe touché doit être recalculé sur tout son historique"

    # Un groupe qui n'a rien reçu n'est ni touché ni perdu.
    bench.con.execute("insert into main.lignes values ('b', 7, '2024-01-03')")
    _, rows = bench.build(spec)
    assert sorted((r[0], r[1]) for r in rows) == [("a", 15), ("b", 7)]


def test_une_agregation_incrementale_ne_laisse_pas_deux_totaux(bench: Bench):
    """Le deuxième build est celui qui révèle : un seul total par groupe.

    Le contrôle acceptait `unique_key: [client, ts]` sur un regroupement par
    `client` seul. `ts` étant une mesure, dbt ne retrouvait pas la ligne déjà
    écrite : après 10 puis 5, la table portait 10 *et* 15 pour le même client.
    La recipe est maintenant refusée à la compilation — ce test vérifie ce que
    donne celle qui la remplace, sur deux vrais builds.
    """
    bench.table(
        "lignes",
        "client varchar, pays varchar, montant integer, ts varchar",
        [("a", "fr", 10, "2024-01-01")],
    )
    spec = {
        "name": "totaux",
        "type": "group",
        "inputs": [{"ref": "lignes"}],
        "group_by": ["client", "pays"],
        "aggregations": [
            {"fn": "sum", "column": "montant", "alias": "total"},
            {"fn": "max", "column": "ts", "alias": "ts"},
        ],
        "output": {
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "unique_key": ["client", "pays"],
            "incremental": {"column": "ts", "operator": "gt"},
        },
    }
    cols, rows = bench.build(spec)
    assert rows == [("a", "fr", 10, "2024-01-01")]

    bench.con.execute("insert into main.lignes values ('a', 'fr', 5, '2024-01-02')")
    cols, rows = bench.build(spec)
    assert len(rows) == 1, f"un seul total par groupe, pas {len(rows)} : {rows}"
    assert rows[0][cols.index("total")] == 15

    # Un groupe voisin entre sans emporter l'autre, et le rapprochement sur
    # deux clés tient : c'est un `exists` corrélé, pas un `(a, b) in (…)`.
    bench.con.execute("insert into main.lignes values ('a', 'es', 7, '2024-01-03')")
    _, rows = bench.build(spec)
    assert sorted((r[0], r[1], r[2]) for r in rows) == [
        ("a", "es", 7),
        ("a", "fr", 15),
    ]


def test_transformer_une_colonne_dont_le_nom_a_un_espace(bench: Bench):
    """L'alias perdait ses guillemets : `round(...) as order total`."""
    bench.table("ventes", '"order total" double', [(100.456,)])
    spec = prepare_spec(
        [{"type": "round", "params": {"column": "order total", "decimals": 1}}],
        ref="ventes",
    )
    cols, rows = bench.run(spec)
    assert rows[0][cols.index("order total")] == pytest.approx(100.5)


@pytest.mark.parametrize("word", ["order", "select", "group", "from", "table", "user"])
def test_une_colonne_qui_porte_un_mot_reserve(bench: Bench, word: str):
    """`order` a la forme d'un identifiant simple, mais n'en est pas un.

    `q()` ne citait que ce qui n'était pas en minuscules-et-underscores : le SQL
    généré sortait `round(order, 1)`, que l'entrepôt refuse de parser.
    """
    bench.table("ventes", f'"{word}" double', [(100.456,)])
    cols, rows = bench.run(
        prepare_spec(
            [{"type": "round", "params": {"column": word, "decimals": 1}}], ref="ventes"
        )
    )
    assert rows[0][cols.index(word)] == pytest.approx(100.5)


def test_une_mesure_qui_porte_un_mot_reserve(bench: Bench):
    """Les alias de sortie aussi : `count(*) as order` ne parse pas."""
    bench.table("lignes", "client varchar", [("a",), ("a",)])
    cols, rows = bench.run(
        {
            "name": "totaux",
            "type": "group",
            "inputs": [{"ref": "lignes"}],
            "group_by": ["client"],
            "aggregations": [{"fn": "count", "column": "*", "alias": "order"}],
            "output": {},
        }
    )
    assert rows[0][cols.index("order")] == 2


def test_une_colonne_de_jointure_qui_porte_un_mot_reserve(bench: Bench):
    bench.table("gauches", 'id integer, "user" varchar', [(1, "jean")])
    bench.table("droites", "id integer, pays varchar", [(1, "fr")])
    cols, rows = bench.run(
        {
            "name": "j",
            "type": "join",
            "inputs": [{"ref": "gauches"}, {"ref": "droites"}],
            "joins": [{"type": "left", "on": [{"left": "id", "right": "id"}]}],
            "select": [
                {"from": "gauches", "column": "user"},
                {"from": "droites", "column": "pays", "as": "select"},
            ],
            "output": {},
        }
    )
    assert rows[0][cols.index("user")] == "jean"
    assert rows[0][cols.index("select")] == "fr"


def test_capitalise_ne_touche_que_la_premiere_lettre(bench: Bench):
    """`initcap` capitalisait chaque mot ; le remplacement, la première lettre.

    Ce n'est pas le même comportement : le test le fige, et l'interface
    l'annonce dans son étiquette et son aide.
    """
    bench.table("gens", "nom varchar", [("jean DUPONT",)])
    cols, rows = bench.run(
        prepare_spec(
            [
                {
                    "type": "text_transform",
                    "params": {"column": "nom", "mode": "capitalize"},
                }
            ],
            ref="gens",
        )
    )
    assert rows[0][cols.index("nom")] == "Jean dupont"


# ----------------------------------------------- incrémental : les retards


def _late_sales(bench: Bench, lookback) -> dict:
    """Deux ventes, puis une troisième qui arrive avec une date antérieure."""
    bench.table(
        "ventes",
        "id integer, ts date",
        [(1, "2024-01-01"), (2, "2024-01-05")],
    )
    inc = {"column": "ts", "operator": "gt"}
    if lookback is not None:
        inc["lookback"] = lookback
    return prepare_spec(
        [{"type": "round", "params": {"column": "id", "decimals": 0}}],
        ref="ventes",
        output={
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "unique_key": ["id"],
            "incremental": inc,
        },
    )


def test_une_ligne_arrivee_en_retard_est_perdue_sans_fenetre_de_reprise(bench: Bench):
    """Le comportement d'avant, tel qu'il est : la ligne ne revient jamais.

    Elle porte une date antérieure au maximum déjà écrit, donc le `> max`
    strict l'écarte — et l'écartera à chaque exécution suivante. Aucun build
    n'échoue : la table est simplement fausse.
    """
    spec = _late_sales(bench, None)
    _, rows = bench.build(spec)
    assert len(rows) == 2

    bench.con.execute("insert into main.ventes values (3, date '2024-01-03')")
    _, rows = bench.build(spec)
    assert len(rows) == 2, "sans fenêtre, la vente du 3 janvier reste dehors"


def test_une_fenetre_de_reprise_rattrape_la_ligne_arrivee_en_retard(bench: Bench):
    spec = _late_sales(bench, {"n": 5, "unit": "day"})
    _, rows = bench.build(spec)
    assert len(rows) == 2

    bench.con.execute("insert into main.ventes values (3, date '2024-01-03')")
    _, rows = bench.build(spec)
    ids = sorted(r[0] for r in rows)
    assert ids == [1, 2, 3], "la fenêtre doit rattraper le retard sans dupliquer"


# ------------------------------------------------- dédoublonnage par clé


@pytest.fixture
def clients(bench: Bench) -> Bench:
    """Trois versions d'un client, une version d'un autre, une date manquante."""
    bench.table(
        "maj_clients",
        "customer_id integer, maj_le date, ville varchar",
        [
            (1, "2024-01-01", "Lyon"),
            (1, "2024-03-01", "Paris"),
            (1, None, "Nulle part"),
            (2, "2024-02-01", "Nantes"),
        ],
    )
    return bench


def _dedup(**params) -> dict:
    base = {
        "keys": ["customer_id"],
        "order_by": [{"column": "maj_le", "descending": True}],
    }
    base.update(params)
    return prepare_spec([{"type": "dedup_key", "params": base}], ref="maj_clients")


def test_dedoublonner_par_cle_garde_la_version_la_plus_recente(clients: Bench):
    """Le cas que « Dédoublonner » ne savait pas faire : une ligne par client."""
    cols, rows = clients.run(_dedup())
    assert len(rows) == 2
    cities = {r[cols.index("customer_id")]: r[cols.index("ville")] for r in rows}
    assert cities == {1: "Paris", 2: "Nantes"}


def test_une_date_manquante_ne_passe_pas_pour_la_plus_recente(clients: Bench):
    """`desc` remonte les NULL en tête sur DuckDB, PostgreSQL et Redshift.

    Sans `nulls last`, la ligne sans date gagnait — et c'est précisément celle
    dont on ne sait rien.
    """
    cols, rows = clients.run(_dedup())
    winner = next(r for r in rows if r[cols.index("customer_id")] == 1)
    assert winner[cols.index("ville")] == "Paris"


def test_dedoublonner_par_cle_garde_toutes_les_colonnes(clients: Bench):
    cols, _ = clients.run(_dedup())
    assert cols == ["customer_id", "maj_le", "ville"]
    assert not any(
        c.startswith("_pliq") for c in cols
    ), "la colonne technique reste dedans"


def test_les_ex_aequo_peuvent_etre_gardes_ou_departages(bench: Bench):
    bench.table(
        "doublons",
        "id integer, maj_le date, v varchar",
        [(1, "2024-01-01", "a"), (1, "2024-01-01", "b")],
    )
    spec = prepare_spec(
        [
            {
                "type": "dedup_key",
                "params": {
                    "keys": ["id"],
                    "order_by": [{"column": "maj_le", "descending": True}],
                    "ties": "one",
                },
            }
        ],
        ref="doublons",
    )
    _, rows = bench.run(spec)
    assert len(rows) == 1, "« une seule » doit trancher même à égalité"

    spec["steps"][0]["params"]["ties"] = "all"
    _, rows = bench.run(spec)
    assert len(rows) == 2, "« toutes » doit garder les ex æquo"


def test_dedoublonner_par_cle_puis_filtrer(clients: Bench):
    """La colonne de rang ne doit pas gêner l'étape suivante."""
    spec = _dedup()
    spec["steps"].append(
        {
            "id": "s2",
            "enabled": True,
            "type": "filter_value",
            "params": {
                "column": "ville",
                "operator": "eq",
                "values": ["Paris"],
                "action": "keep",
            },
        }
    )
    cols, rows = clients.run(spec)
    assert len(rows) == 1 and rows[0][cols.index("ville")] == "Paris"


# ------------------------------------------------------ fonctions de fenêtre


@pytest.fixture
def daily_sales(bench: Bench) -> Bench:
    bench.table(
        "ventes_jour",
        "client varchar, jour date, montant double",
        [
            ("A", "2024-01-01", 10.0),
            ("A", "2024-01-02", 20.0),
            ("A", "2024-01-03", 30.0),
            ("B", "2024-01-01", 100.0),
        ],
    )
    return bench


def _window_of(**params) -> dict:
    return prepare_spec(
        [{"type": "window_function", "params": params}], ref="ventes_jour"
    )


def test_le_cumul_additionne_ce_qui_precede(daily_sales: Bench):
    cols, rows = daily_sales.run(
        _window_of(
            fn="running_sum",
            column="montant",
            partition_by=["client"],
            order_by=[{"column": "jour"}],
            into="cumul",
        )
    )
    by_day = {
        (r[cols.index("client")], str(r[cols.index("jour")])): r[cols.index("cumul")]
        for r in rows
    }
    assert by_day[("A", "2024-01-01")] == 10
    assert by_day[("A", "2024-01-03")] == 60
    assert by_day[("B", "2024-01-01")] == 100, "chaque client repart de zéro"


def test_le_total_de_fenetre_n_est_pas_un_cumul(daily_sales: Bench):
    """Sans ordre, `sum() over (partition by …)` donne le total du groupe.

    Avec un ordre, l'entrepôt en ferait un cumul — un total différent sur
    chaque ligne. C'est pour ça que l'étape refuse un tri ici.
    """
    cols, rows = daily_sales.run(
        _window_of(
            fn="sum", column="montant", partition_by=["client"], into="total_client"
        )
    )
    totals = {r[cols.index("client")]: r[cols.index("total_client")] for r in rows}
    assert totals == {"A": 60, "B": 100}


def test_la_valeur_precedente_et_le_classement(daily_sales: Bench):
    spec = _window_of(
        fn="lag",
        column="montant",
        partition_by=["client"],
        order_by=[{"column": "jour"}],
        into="veille",
    )
    spec["steps"].append(
        {
            "id": "s2",
            "enabled": True,
            "type": "window_function",
            "params": {
                "fn": "rank",
                "partition_by": ["client"],
                "order_by": [{"column": "montant", "descending": True}],
                "into": "rang",
            },
        }
    )
    cols, rows = daily_sales.run(spec)
    a3 = next(
        r
        for r in rows
        if r[cols.index("client")] == "A" and str(r[cols.index("jour")]) == "2024-01-03"
    )
    assert a3[cols.index("veille")] == 20
    assert a3[cols.index("rang")] == 1
    a1 = next(
        r
        for r in rows
        if r[cols.index("client")] == "A" and str(r[cols.index("jour")]) == "2024-01-01"
    )
    assert a1[cols.index("veille")] is None, "la première ligne n'a pas de veille"


def test_la_moyenne_glissante_porte_sur_les_n_dernieres_lignes(daily_sales: Bench):
    cols, rows = daily_sales.run(
        _window_of(
            fn="moving_avg",
            column="montant",
            partition_by=["client"],
            order_by=[{"column": "jour"}],
            window_rows=2,
            into="moyenne_2",
        )
    )
    by_day = {
        (r[cols.index("client")], str(r[cols.index("jour")])): r[
            cols.index("moyenne_2")
        ]
        for r in rows
    }
    assert by_day[("A", "2024-01-01")] == 10
    assert by_day[("A", "2024-01-02")] == 15, "(10 + 20) / 2"
    assert by_day[("A", "2024-01-03")] == 25, "(20 + 30) / 2"


# ------------------------------------------------------- pivot / dépivotage


def test_depivoter_replie_les_colonnes_en_lignes(bench: Bench):
    bench.table(
        "ca_mensuel",
        "client varchar, jan double, fev double",
        [("A", 10.0, 20.0), ("B", 1.0, 2.0)],
    )
    cols, rows = bench.run(
        prepare_spec(
            [
                {
                    "type": "unpivot",
                    "params": {
                        "columns": ["jan", "fev"],
                        "name_into": "mois",
                        "value_into": "ca",
                    },
                }
            ],
            ref="ca_mensuel",
        )
    )
    assert cols == ["client", "mois", "ca"]
    assert len(rows) == 4
    found = {(r[0], r[1]): r[2] for r in rows}
    assert found[("A", "jan")] == 10 and found[("B", "fev")] == 2


def test_pivoter_deplie_les_lignes_en_colonnes(bench: Bench):
    bench.table(
        "ca_long",
        "client varchar, mois varchar, ca double",
        [("A", "jan", 10.0), ("A", "fév", 20.0), ("B", "jan", 1.0)],
    )
    cols, rows = bench.run(
        prepare_spec(
            [
                {
                    "type": "pivot",
                    "params": {
                        "key_columns": ["client"],
                        "name_column": "mois",
                        "value_column": "ca",
                        "values": ["jan", "fév"],
                        "aggregate": "sum",
                        "prefix": "ca_",
                    },
                }
            ],
            ref="ca_long",
        )
    )
    assert cols == [
        "client",
        "ca_jan",
        "ca_fev",
    ], "les accents sont repliés, pas hachés"
    by_customer = {r[0]: (r[1], r[2]) for r in rows}
    assert by_customer["A"] == (10, 20)
    assert by_customer["B"] == (1, None), "un mois absent reste vide, pas zéro"


def test_pivoter_puis_depivoter_revient_au_point_de_depart(bench: Bench):
    bench.table(
        "ca_long2",
        "client varchar, mois varchar, ca double",
        [("A", "jan", 10.0), ("A", "fev", 20.0)],
    )
    _cols, rows = bench.run(
        prepare_spec(
            [
                {
                    "type": "pivot",
                    "params": {
                        "key_columns": ["client"],
                        "name_column": "mois",
                        "value_column": "ca",
                        "values": ["jan", "fev"],
                        "aggregate": "sum",
                        "prefix": "m_",
                    },
                },
                {
                    "type": "unpivot",
                    "params": {
                        "columns": ["m_jan", "m_fev"],
                        "name_into": "mois",
                        "value_into": "ca",
                    },
                },
            ],
            ref="ca_long2",
        )
    )
    assert sorted((r[1], r[2]) for r in rows) == [("m_fev", 20), ("m_jan", 10)]


# ------------------------------------------------- diagnostic de jointure


def _diagnose(bench: Bench, spec: dict) -> list[dict]:
    """Exécute vraiment la requête de diagnostic, et lit ce qu'elle dit."""
    aliases = [rcp.input_alias(i, n) for n, i in enumerate(spec["inputs"])]
    sql, plan = join_diagnostics.diagnose_join_sql(spec, bench.columns(*aliases))
    cols, rows = bench.sql(resolve_refs_for_test(sql, bench.manifest))
    return join_diagnostics.join_diagnosis(plan, dict(zip(cols, rows[0], strict=True)))


def _join_spec_fixture(jtype: str = "left") -> dict:
    return {
        "name": "commandes_clients",
        "type": "join",
        "inputs": [
            {"ref": "commandes", "alias": "commandes"},
            {"ref": "clients", "alias": "clients"},
        ],
        "joins": [{"type": jtype, "on": [{"left": "client_id", "right": "client_id"}]}],
        "output": {},
    }


def test_le_diagnostic_annonce_la_multiplication_des_lignes(bench: Bench):
    """« Cette jointure multiplie les commandes par 3 » — ce qu'un SQL valide tait."""
    bench.table("commandes", "id integer, client_id integer", [(1, 10), (2, 11)])
    bench.table(
        "clients",
        "client_id integer, adresse varchar",
        [(10, "a"), (10, "b"), (10, "c"), (11, "d")],
    )
    findings = _diagnose(bench, _join_spec_fixture())

    assert findings[0]["level"] == "bad"
    assert "multiplie" in findings[0]["title"]
    assert "2 lignes en entrée, 4 en sortie" in findings[0]["detail"]
    assert any(
        "n'est pas unique" in c["title"] and "jusqu'à 3" in c["title"] for c in findings
    )


def test_le_diagnostic_rassure_quand_la_cle_est_unique(bench: Bench):
    bench.table("commandes", "id integer, client_id integer", [(1, 10), (2, 11)])
    bench.table("clients", "client_id integer, nom varchar", [(10, "a"), (11, "b")])
    findings = _diagnose(bench, _join_spec_fixture())

    assert findings[0]["level"] == "ok"
    assert "ne change pas le nombre de lignes" in findings[0]["title"]
    assert any(
        "Relation de 1 à 1 sur client_id = client_id" in c["title"] for c in findings
    )
    assert all(c["level"] == "ok" for c in findings)


def test_le_diagnostic_compte_les_lignes_sans_correspondance(bench: Bench):
    bench.table("commandes", "id integer, client_id integer", [(1, 10), (2, 99)])
    bench.table("clients", "client_id integer, nom varchar", [(10, "a")])

    findings = _diagnose(bench, _join_spec_fixture("left"))
    orphans = next(c for c in findings if "correspondance" in c["title"])
    assert "1 lignes de « commandes » sur 2" in orphans["title"]
    assert "vides" in orphans["detail"]

    findings = _diagnose(bench, _join_spec_fixture("inner"))
    orphans = next(c for c in findings if "correspondance" in c["title"])
    assert orphans["level"] == "bad"
    assert "disparaissent" in orphans["detail"]


def test_une_cle_vide_ne_compte_pas_comme_une_multiplication(bench: Bench):
    """NULL ne s'apparie à rien : mille lignes sans clé ne multiplient rien.

    Les compter dans le maximum par clé annoncerait une explosion qui n'aura
    pas lieu — le genre d'alerte qui apprend à ignorer les alertes.
    """
    bench.table("commandes", "id integer, client_id integer", [(1, 10)])
    bench.table(
        "clients",
        "client_id integer, nom varchar",
        [(10, "a"), (None, "b"), (None, "c"), (None, "d")],
    )
    findings = _diagnose(bench, _join_spec_fixture())

    assert findings[0]["level"] == "ok", "aucune ligne n'est dupliquée"
    assert any("Relation de 1 à 1" in c["title"] for c in findings)
    assert any(
        "3 lignes de « clients » ont une clé vide" in c["title"] for c in findings
    )


def test_le_diagnostic_lit_les_deux_cotes_d_une_jointure_droite(bench: Bench):
    """Avec `right`, c'est l'autre côté qui commande.

    Ne mesurer que la table rapportée disait l'inverse de la vérité : les
    lignes de « clients » sans commande sont gardées, celles de « commandes »
    sans client sont perdues.
    """
    bench.table("commandes", "id integer, client_id integer", [(1, 10), (2, 99)])
    bench.table("clients", "client_id integer, nom varchar", [(10, "a"), (11, "b")])
    findings = _diagnose(bench, _join_spec_fixture("right"))

    lost_finding = next(c for c in findings if "« commandes » sur 2" in c["title"])
    assert lost_finding["level"] == "bad" and "disparaissent" in lost_finding["detail"]

    kept_finding = next(c for c in findings if "« clients » sur 2" in c["title"])
    assert kept_finding["level"] == "warn" and "restent" in kept_finding["detail"]


def test_le_diagnostic_nomme_une_relation_de_n_a_n(bench: Bench):
    """Ni un côté ni l'autre n'est unique : presque toujours une clé incomplète."""
    bench.table("commandes", "id integer, client_id integer", [(1, 10), (2, 10)])
    bench.table("clients", "client_id integer, nom varchar", [(10, "a"), (10, "b")])
    findings = _diagnose(bench, _join_spec_fixture())

    assert "multiplie les lignes de « commandes » par 2" in findings[0]["title"]
    nn = next(c for c in findings if "N à N" in c["title"])
    assert nn["level"] == "bad"
    assert "il manque une colonne" in nn["detail"]

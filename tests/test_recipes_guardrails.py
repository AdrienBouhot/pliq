"""Ce que l'atelier doit refuser.

Une recipe fausse doit être arrêtée à la saisie, avec un message qui dit quoi
corriger — pas trois minutes plus tard au milieu d'un `dbt build`.
"""

from __future__ import annotations

import pytest
from jinja2 import Environment

from pliq import recipes as rcp
from pliq.recipes import ColumnState, RecipeError

from .conftest import Bench, prepare_spec


def compile_ko(bench: Bench, spec: dict) -> str:
    with pytest.raises(RecipeError) as exc:
        bench.compile(spec)
    return str(exc.value)


# --------------------------------------------------------------- identifiants


@pytest.mark.parametrize(
    "name", ["", "2eme", "mon modèle", "mon-modele", "select *", "a b"]
)
def test_nom_de_modele_invalide(name: str):
    with pytest.raises(RecipeError, match="invalide"):
        rcp.validate_name(name)


def test_colonne_vide_refusee():
    with pytest.raises(RecipeError):
        rcp.q("")


def test_identifiant_exotique_est_quote():
    assert rcp.q("ma colonne") == '"ma colonne"'


@pytest.mark.parametrize("word", ["order", "select", "group", "user", "table"])
def test_un_mot_reserve_est_quote(word: str):
    """Il a la forme d'un identifiant simple, mais l'entrepôt ne l'accepte pas."""
    assert rcp.q(word) == f'"{word}"'


def test_le_guillemet_suit_le_dialecte():
    """BigQuery cite avec des backticks : `"order"` y serait une chaîne."""
    with rcp.using_dialect("bigquery"):
        assert rcp.q("order") == "`order`"
        assert rcp.q("ma colonne") == "`ma colonne`"
    assert rcp.q("order") == '"order"'
    assert rcp.q('bizarre"nom') == '"bizarre""nom"'
    assert rcp.q("simple_nom") == "simple_nom"


def test_litteraux_texte_echappes():
    assert rcp.lit("l'été") == "'l''été'"
    assert rcp.lit(None) == "null"
    assert rcp.lit(True) == "true"


# ------------------------------------------------------------------- étapes


def test_colonne_inexistante_nomme_les_colonnes_disponibles(orders: Bench):
    msg = compile_ko(
        orders,
        prepare_spec(
            [
                {"type": "round", "params": {"column": "montant", "decimals": 0}},
            ]
        ),
    )
    assert "montant" in msg and "amount_eur" in msg


def test_colonne_supprimee_a_l_etape_precedente(orders: Bench):
    msg = compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "keep_delete",
                    "params": {"columns": ["status"], "action": "delete"},
                },
                {
                    "type": "text_transform",
                    "params": {"column": "status", "mode": "lower"},
                },
            ]
        ),
    )
    assert "n'existe pas à cette étape" in msg


def test_formule_sql_invalide(orders: Bench):
    msg = compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "formula",
                    "params": {"into": "x", "expression": "select from where"},
                },
            ]
        ),
    )
    assert "SQL invalide" in msg


def test_condition_invalide(orders: Bench):
    msg = compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "filter_formula",
                    "params": {"condition": "amount_eur >", "action": "keep"},
                },
            ]
        ),
    )
    assert "invalide" in msg


def test_expression_vide(orders: Bench):
    assert "vide" in compile_ko(
        orders,
        prepare_spec(
            [
                {"type": "formula", "params": {"into": "x", "expression": "   "}},
            ]
        ),
    )


def test_etape_inconnue(orders: Bench):
    assert "Étape inconnue" in compile_ko(
        orders,
        prepare_spec(
            [
                {"type": "faire_le_cafe", "params": {}},
            ]
        ),
    )


def test_supprimer_toutes_les_colonnes(orders: Bench):
    assert "aucune colonne" in compile_ko(
        orders,
        prepare_spec(
            [
                {"type": "keep_delete", "params": {"columns": [], "action": "keep"}},
            ]
        ),
    )


def test_concatener_une_seule_colonne(orders: Bench):
    assert "au moins deux" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "concat_columns",
                    "params": {"columns": ["status"], "into": "x"},
                },
            ]
        ),
    )


def test_decouper_trop_de_morceaux(orders: Bench):
    assert "entre 1 et 10" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "split_column",
                    "params": {"column": "status", "separator": ",", "count": 42},
                },
            ]
        ),
    )


def test_filtrer_sans_valeur(orders: Bench):
    assert "aucune valeur" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "filter_value",
                    "params": {"column": "status", "operator": "in", "values": []},
                },
            ]
        ),
    )


def test_operateur_de_filtre_inconnu(orders: Bench):
    assert "Opérateur" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "filter_value",
                    "params": {
                        "column": "status",
                        "operator": "ressemble_a",
                        "values": ["x"],
                    },
                },
            ]
        ),
    )


def test_transformation_de_texte_inconnue(orders: Bench):
    assert "inconnue" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "text_transform",
                    "params": {"column": "status", "mode": "verlan"},
                },
            ]
        ),
    )


def test_composant_de_date_inconnu(orders: Bench):
    assert "inconnu" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "extract_date_parts",
                    "params": {"column": "ordered_at", "parts": ["decade"]},
                },
            ]
        ),
    )


@pytest.mark.parametrize(
    "wrong_type",
    [
        "varchar); drop table stg_orders; --",
        "'; select 1",
        "",
    ],
)
def test_changer_le_type_refuse_ce_qui_n_est_pas_un_type(
    orders: Bench, wrong_type: str
):
    assert "Type invalide" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "change_type",
                    "params": {"column": "status", "to": wrong_type},
                },
            ]
        ),
    )


def test_renommer_vers_un_nom_invalide(orders: Bench):
    assert "invalide" in compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "rename",
                    "params": {"renames": [{"from": "status", "to": "mon statut"}]},
                },
            ]
        ),
    )


# ----------------------------------------------------------- nombre d'entrées


def test_prepare_veut_exactement_une_entree(orders: Bench):
    spec = prepare_spec([])
    spec["inputs"] = spec["inputs"] * 2
    assert "exactement un" in compile_ko(orders, spec)


def test_join_veut_au_moins_deux_entrees(orders: Bench):
    assert "au moins deux" in compile_ko(
        orders,
        {
            "name": "j",
            "type": "join",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "output": {},
        },
    )


def test_stack_veut_au_moins_deux_entrees(orders: Bench):
    assert "au moins deux" in compile_ko(
        orders,
        {
            "name": "s",
            "type": "stack",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "output": {},
        },
    )


def test_type_de_recipe_inconnu(orders: Bench):
    assert "non géré" in compile_ko(
        orders,
        {
            "name": "x",
            "type": "pivot",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "output": {},
        },
    )


def test_recipe_sql_vide(orders: Bench):
    assert "vide" in compile_ko(
        orders,
        {
            "name": "x",
            "type": "sql",
            "sql": "  ",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "output": {},
        },
    )


# ------------------------------------------------------------------ jointure


@pytest.fixture
def two_tables(orders: Bench) -> Bench:
    orders.table(
        "stg_customers", "customer_id integer, status varchar", [(10, "actif")]
    )
    return orders


def _join(select=None, joins=None) -> dict:
    return {
        "name": "j",
        "type": "join",
        "inputs": [
            {"ref": "stg_orders", "alias": "stg_orders"},
            {"ref": "stg_customers", "alias": "stg_customers"},
        ],
        "joins": (
            joins
            if joins is not None
            else [
                {
                    "type": "left",
                    "on": [{"left": "customer_id", "right": "customer_id"}],
                }
            ]
        ),
        "select": select or [],
        "output": {},
    }


def test_jointure_sans_cle(two_tables: Bench):
    assert "aucune clé" in compile_ko(
        two_tables, _join(joins=[{"type": "left", "on": []}])
    )


def test_type_de_jointure_inconnu(two_tables: Bench):
    assert "inconnu" in compile_ko(
        two_tables,
        _join(
            joins=[
                {
                    "type": "diagonal",
                    "on": [{"left": "customer_id", "right": "customer_id"}],
                }
            ]
        ),
    )


def test_deux_colonnes_de_sortie_du_meme_nom_sont_refusees(two_tables: Bench):
    """C'est l'erreur classique de la jointure : dbt build échouerait plus tard."""
    msg = compile_ko(
        two_tables,
        _join(
            [
                {"from": "stg_orders", "column": "status"},
                {"from": "stg_customers", "column": "status"},
            ]
        ),
    )
    assert "status" in msg and "Renommez" in msg


def test_jointure_sur_un_dataset_inconnu(two_tables: Bench):
    assert "inconnu" in compile_ko(
        two_tables,
        _join(
            [
                {"from": "stg_clients", "column": "status"},
            ]
        ),
    )


# --------------------------------------------------------------------- group


def test_group_sans_mesure(orders: Bench):
    assert "au moins une mesure" in compile_ko(
        orders,
        {
            "name": "g",
            "type": "group",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "group_by": ["customer_id"],
            "aggregations": [],
            "output": {},
        },
    )


def test_agregation_inconnue(orders: Bench):
    assert "inconnue" in compile_ko(
        orders,
        {
            "name": "g",
            "type": "group",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "group_by": [],
            "aggregations": [
                {"fn": "stddev_pop", "column": "amount_eur", "alias": "x"}
            ],
            "output": {},
        },
    )


def test_mesure_sans_nom(orders: Bench):
    assert "invalide" in compile_ko(
        orders,
        {
            "name": "g",
            "type": "group",
            "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
            "group_by": [],
            "aggregations": [{"fn": "count", "column": "*", "alias": ""}],
            "output": {},
        },
    )


# ------------------------------------------------ le config(), écrit en Jinja


@pytest.mark.parametrize(
    "tag",
    [
        "chiffre d'affaires",
        'guillemets "doubles"',
        "antislash \\ perdu",
        "l'un et \"l'autre\"",
    ],
)
def test_un_tag_ponctue_rend_du_jinja_valide(tag: str):
    """Le `config()` est du Jinja avant d'être du SQL.

    Les tags sont la seule valeur du bloc qui ne passe ni par `validate_name`
    ni par une liste fermée : c'est du texte libre, saisi dans l'atelier.
    Interpolé entre apostrophes, « chiffre d'affaires » fermait la chaîne au
    milieu du mot, et le fichier écrit ne parsait plus.
    """
    block = rcp.config_block({"materialized": "table", "tags": [tag]})
    # Rendu comme dbt le rendrait : le bloc doit parser, et `config()` doit
    # recevoir le tag tel qu'il a été saisi — échapper n'est pas réécrire.
    seen: dict = {}
    env = Environment()
    env.globals["config"] = lambda **kw: seen.update(kw) or ""
    assert "select 1" in env.from_string(block + "select 1").render()
    assert seen["tags"] == [tag]
    assert seen["materialized"] == "table"


def test_une_cle_unique_reste_un_littéral_simple():
    """Le reste du bloc est validé en amont : rien ne doit changer de forme."""
    block = rcp.config_block(
        {
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "unique_key": ["order_id"],
        }
    )
    assert "unique_key='order_id'" in block


# --------------------------------------------------------------- incrémental


def test_incremental_sans_cle_est_bloque():
    for strategy in ("delete+insert", "merge"):
        with pytest.raises(RecipeError, match="clé unique"):
            rcp.config_block(
                {
                    "materialized": "incremental",
                    "incremental_strategy": strategy,
                    "unique_key": [],
                }
            )


def test_incremental_append_sans_cle_est_accepte():
    out = rcp.config_block(
        {
            "materialized": "incremental",
            "incremental_strategy": "append",
            "unique_key": [],
        }
    )
    assert "incremental_strategy='append'" in out and "unique_key" not in out


def test_materialisation_inconnue():
    with pytest.raises(RecipeError, match="Matérialisation inconnue"):
        rcp.config_block({"materialized": "materialized_view"})


def test_strategie_inconnue():
    with pytest.raises(RecipeError, match="Stratégie"):
        rcp.config_block(
            {
                "materialized": "incremental",
                "incremental_strategy": "upsert",
                "unique_key": ["id"],
            }
        )


def test_on_schema_change_inconnu():
    with pytest.raises(RecipeError, match="on_schema_change"):
        rcp.config_block(
            {
                "materialized": "incremental",
                "incremental_strategy": "append",
                "on_schema_change": "panique",
            }
        )


# ------------------------------------------------- incrémental : ce qui est faux


def _group_incremental(**out) -> dict:
    return {
        "name": "totaux",
        "type": "group",
        "inputs": [{"ref": "lignes"}],
        "group_by": out.pop("group_by", ["client"]),
        "aggregations": [
            {"fn": "sum", "column": "montant", "alias": "total"},
            {"fn": "max", "column": "ts", "alias": "ts"},
        ],
        "output": {
            "materialized": "incremental",
            "incremental": {"column": out.pop("column", "ts"), "operator": "gt"},
            **out,
        },
    }


@pytest.fixture
def lines(bench: Bench) -> Bench:
    bench.table(
        "lignes",
        "client varchar, montant integer, ts varchar",
        [("a", 10, "2024-01-01")],
    )
    return bench


def test_une_agregation_incrementale_en_append_est_refusee(lines: Bench):
    """Recalculer un groupe et l'ajouter double son total."""
    msg = compile_ko(
        lines,
        _group_incremental(incremental_strategy="append", unique_key=["client"]),
    )
    assert "append" in msg and "delete+insert" in msg


def test_une_agregation_incrementale_sans_regroupement_est_refusee(lines: Bench):
    msg = compile_ko(
        lines,
        _group_incremental(
            group_by=[], incremental_strategy="delete+insert", unique_key=["client"]
        ),
    )
    assert "regroupement" in msg


def test_les_cles_uniques_doivent_couvrir_le_regroupement(lines: Bench):
    msg = compile_ko(
        lines,
        _group_incremental(incremental_strategy="delete+insert", unique_key=["autre"]),
    )
    assert "client" in msg


def test_une_cle_unique_qui_porte_une_mesure_est_refusee(lines: Bench):
    """Le contrôle ne regardait que le sens « il manque une clé ».

    `group_by: [client]` avec `unique_key: [client, ts]` passait, alors que
    `ts` est une *mesure* : elle change d'une exécution à l'autre. dbt cherche
    alors (client, ts_nouveau), ne trouve rien à retirer, et insère un second
    total pour le même client. Deux totaux concurrents dans la table, et aucun
    build en échec pour le dire.
    """
    msg = compile_ko(
        lines,
        _group_incremental(
            incremental_strategy="delete+insert", unique_key=["client", "ts"]
        ),
    )
    assert "ts" in msg and "regroupement" in msg


def test_une_cle_unique_qui_couvre_pile_le_regroupement_passe(lines: Bench):
    """Le contre-exemple : la mesure de borne n'a pas à être dans les clés."""
    sql = lines.compile(
        _group_incremental(incremental_strategy="delete+insert", unique_key=["client"])
    )
    assert "unique_key='client'" in sql


def test_la_colonne_incrementale_doit_sortir_de_l_agregation(lines: Bench):
    """`max(maj)` sur la table cible ne trouve rien si `maj` n'y est pas."""
    msg = compile_ko(
        lines,
        _group_incremental(
            column="maj", incremental_strategy="delete+insert", unique_key=["client"]
        ),
    )
    assert "maj" in msg


def _prepare_incremental(steps: list[dict], column: str = "ordered_at") -> dict:
    return prepare_spec(
        steps,
        output={
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "unique_key": ["order_id"],
            "incremental": {"column": column, "operator": "gt"},
        },
    )


def test_la_colonne_incrementale_doit_sortir_du_script(orders: Bench):
    """Le piège se referme au *deuxième* build, jamais au premier.

    Le filtre compare deux choses : `ordered_at` telle qu'elle arrive, et
    `max(ordered_at)` lu dans la table déjà écrite. Une étape qui la renomme
    laisse la seconde chercher une colonne qui n'existe plus dans la sortie.
    Or `is_incremental()` est faux au premier build : le bloc n'est pas
    compilé, le modèle passe, et c'est l'exécution suivante qui casse — loin
    de la cause. La jointure et l'empilement faisaient déjà ce contrôle.
    """
    msg = compile_ko(
        orders,
        _prepare_incremental(
            [
                {
                    "type": "rename",
                    "params": {"renames": [{"from": "ordered_at", "to": "loaded_at"}]},
                }
            ]
        ),
    )
    assert "ordered_at" in msg


def test_la_colonne_incrementale_ne_peut_pas_non_plus_etre_supprimee(orders: Bench):
    """Même symptôme, sans le moindre renommage : la borne doit sortir."""
    msg = compile_ko(
        orders,
        _prepare_incremental(
            [
                {
                    "type": "keep_delete",
                    "params": {"action": "delete", "columns": ["ordered_at"]},
                }
            ]
        ),
    )
    assert "ordered_at" in msg


def test_la_colonne_incrementale_doit_exister_a_l_entree(orders: Bench):
    """C'est dans le CTE d'entrée que le filtre la compare."""
    msg = compile_ko(orders, _prepare_incremental([], column="jamais_vue"))
    assert "jamais_vue" in msg


def test_un_script_qui_garde_sa_borne_compile(orders: Bench):
    """Le contre-exemple : renommer *autre chose* ne change rien."""
    sql = orders.compile(
        _prepare_incremental(
            [
                {
                    "type": "rename",
                    "params": {"renames": [{"from": "country", "to": "pays"}]},
                }
            ]
        )
    )
    assert "max(ordered_at)" in sql


def test_un_apercu_partiel_ne_reclame_pas_encore_la_borne(orders: Bench):
    """Un aperçu s'arrête au milieu : la borne peut n'être rétablie qu'après."""
    spec = _prepare_incremental(
        [
            {
                "type": "rename",
                "params": {"renames": [{"from": "ordered_at", "to": "provisoire"}]},
            },
            {
                "type": "rename",
                "params": {"renames": [{"from": "provisoire", "to": "ordered_at"}]},
            },
        ]
    )
    cols = {"stg_orders": [{"name": "ordered_at", "type": "VARCHAR"}]}
    assert rcp.compile_prepare(spec, cols, upto=1)[0]
    assert rcp.compile_prepare(spec, cols)[0]


def test_une_fenetre_ne_se_calcule_pas_sur_un_arrivage(orders: Bench):
    """Le filtre écarte l'historique dont la fenêtre a besoin pour être juste.

    Un cumul incrémental ne voit que les lignes du jour : il repart de zéro à
    chaque exécution. Le SQL est valide, `dbt build` passe, et la colonne est
    fausse — c'est exactement ce qu'un refus à la saisie doit éviter.
    """
    msg = compile_ko(
        orders,
        _prepare_incremental(
            [
                {
                    "type": "window_function",
                    "params": {
                        "fn": "running_sum",
                        "column": "amount_eur",
                        "into": "total",
                        "order_by": [{"column": "order_id"}],
                    },
                }
            ]
        ),
    )
    assert "Cumul" in msg and "table" in msg


def test_la_meme_fenetre_passe_en_table(orders: Bench):
    """Le contre-exemple : sans filtre incrémental, rien ne manque au calcul."""
    spec = _prepare_incremental(
        [
            {
                "type": "window_function",
                "params": {
                    "fn": "running_sum",
                    "column": "amount_eur",
                    "into": "total",
                    "order_by": [{"column": "order_id"}],
                },
            }
        ]
    )
    spec["output"] = {"materialized": "table"}
    assert "sum(" in orders.compile(spec)


def test_la_borne_incrementale_ne_peut_pas_etre_recalculee(orders: Bench):
    """Le nom survit, la valeur non : le filtre compare deux échelles.

    `order_id + 100` écrit dans `order_id` laisse bien un `order_id` en sortie
    — le contrôle de nom passe. Mais la borne lue vaut 104 quand la valeur
    d'entrée vaut 4 : `5 > 104` est faux, et la ligne neuve n'entre jamais.
    """
    msg = compile_ko(
        orders,
        _prepare_incremental(
            [
                {
                    "type": "formula",
                    "params": {"into": "ordered_at", "expression": "ordered_at || 'x'"},
                }
            ],
        ),
    )
    assert "ordered_at" in msg and "échelle" in msg


def test_une_borne_reconstruite_sous_le_meme_nom_est_refusee(orders: Bench):
    """Retirer la borne puis recréer son nom : même piège, sans transformation."""
    msg = compile_ko(
        orders,
        _prepare_incremental(
            [
                {
                    "type": "keep_delete",
                    "params": {"action": "delete", "columns": ["ordered_at"]},
                },
                {
                    "type": "formula",
                    "params": {"into": "ordered_at", "expression": "'2099-01-01'"},
                },
            ]
        ),
    )
    assert "ordered_at" in msg


def test_transformer_une_autre_colonne_que_la_borne_reste_permis(orders: Bench):
    """Le contre-exemple : c'est la borne qui est protégée, pas le script."""
    sql = orders.compile(
        _prepare_incremental(
            [
                {
                    "type": "text_transform",
                    "params": {"column": "country", "mode": "lower"},
                }
            ]
        )
    )
    assert "max(ordered_at)" in sql


def _dedup_incremental(
    strategy: str, unique_keys: list[str], keys=("customer_id",)
) -> dict:
    return prepare_spec(
        [
            {
                "type": "dedup_key",
                "params": {
                    "keys": list(keys),
                    "order_by": [{"column": "ordered_at", "descending": True}],
                },
            }
        ],
        output={
            "materialized": "incremental",
            "incremental_strategy": strategy,
            "unique_key": unique_keys,
            "incremental": {"column": "ordered_at", "operator": "gt"},
        },
    )


def test_dedoublonner_par_cle_veut_une_sortie_qui_remplace_par_cette_cle(orders: Bench):
    """« Une ligne par clé » ne tient que si la sortie retire celle d'hier.

    En `append`, la ligne déjà écrite pour ce client reste en place et celle de
    l'arrivage s'ajoute à côté : deux lignes pour la même clé, aucune erreur.
    """
    msg = compile_ko(orders, _dedup_incremental("append", []))
    assert "append" in msg and "delete+insert" in msg


def test_les_cles_uniques_doivent_couvrir_le_dedoublonnage(orders: Bench):
    """Le cas de perte : dbt retire des lignes que l'arrivage ne réécrit pas.

    Dédoublonner sur (client, statut) en ne remplaçant que par `customer_id`
    efface les autres statuts de ce client — et rien ne les réinsère.
    """
    msg = compile_ko(
        orders,
        _dedup_incremental(
            "delete+insert", ["customer_id"], keys=("customer_id", "status")
        ),
    )
    assert "status" in msg and "couvrir" in msg


def test_les_cles_uniques_ne_portent_que_le_dedoublonnage(orders: Bench):
    """L'autre bord : une clé unique plus large ne retrouve plus la ligne écrite."""
    msg = compile_ko(
        orders, _dedup_incremental("delete+insert", ["customer_id", "order_id"])
    )
    assert "order_id" in msg


def test_un_dedoublonnage_incremental_bien_appareille_compile(orders: Bench):
    """Le contre-exemple : clé unique et clé de dédoublonnage confondues."""
    sql = orders.compile(_dedup_incremental("delete+insert", ["customer_id"]))
    assert "row_number()" in sql and "max(ordered_at)" in sql


def test_une_borne_large_avec_append_est_refusee():
    """Une borne « ≥ » est la fenêtre de reprise, de largeur zéro.

    Elle relit les lignes assises sur le maximum déjà écrit ; `append` les
    ajoute une seconde fois, et la table gagne un doublon par exécution. Aucune
    étape n'est nécessaire pour que ça arrive : un script vide suffit.
    """
    with pytest.raises(RecipeError, match="append"):
        rcp.incremental_where(
            {
                "materialized": "incremental",
                "incremental_strategy": "append",
                "incremental": {"column": "ordered_at", "operator": "gte"},
            }
        )


def test_une_borne_large_avec_une_strategie_qui_remplace_est_permise():
    """Le contre-exemple : la clé unique rattrape les lignes relues."""
    where = rcp.incremental_where(
        {
            "materialized": "incremental",
            "incremental_strategy": "delete+insert",
            "unique_key": ["order_id"],
            "incremental": {"column": "ordered_at", "operator": "gte"},
        }
    )
    assert ">= (select max" in where


def test_un_empilement_incremental_veut_la_colonne_partout(bench: Bench):
    """Sans la colonne de borne, l'entrée se réempilerait en entier."""
    bench.table("ventes_fr", "id integer, ts varchar", [(1, "2024-01-01")])
    bench.table("ventes_es", "id integer", [(2,)])
    msg = compile_ko(
        bench,
        {
            "name": "ventes",
            "type": "stack",
            "inputs": [{"ref": "ventes_fr"}, {"ref": "ventes_es"}],
            "mode": "all",
            "output": {
                "materialized": "incremental",
                "incremental_strategy": "append",
                "incremental": {"column": "ts", "operator": "gt"},
            },
        },
    )
    assert "ts" in msg and "ventes_es" in msg


# ------------------------------------------- incrémental : jointures


def _join_incremental(jtype: str = "left", select=None, column: str = "ts") -> dict:
    return {
        "name": "lignes_jointes",
        "type": "join",
        "inputs": [{"ref": "gauches"}, {"ref": "droites"}],
        "joins": [{"type": jtype, "on": [{"left": "id", "right": "id"}]}],
        "select": (
            select
            if select is not None
            else [
                {"from": "gauches", "column": "id"},
                {"from": "gauches", "column": "ts"},
            ]
        ),
        "output": {
            "materialized": "incremental",
            "incremental_strategy": "append",
            "incremental": {"column": column, "operator": "gt"},
        },
    }


@pytest.fixture
def both_sides(bench: Bench) -> Bench:
    bench.table("gauches", "id integer, ts integer", [(1, 1)])
    bench.table("droites", "id integer, ts integer", [(1, 1)])
    return bench


@pytest.mark.parametrize("jtype", ["right", "full"])
def test_une_jointure_droite_incrementale_est_refusee(both_sides: Bench, jtype: str):
    """Le filtre incrémental ne borne que la première entrée.

    Une ligne de droite déjà écrite ressort comme non appariée dès que sa
    contrepartie de gauche est filtrée : le deuxième build la réémet sans
    qu'aucune source n'ait bougé, et `append` la duplique.
    """
    msg = compile_ko(both_sides, _join_incremental(jtype))
    assert jtype in msg and "droites" in msg


@pytest.mark.parametrize("jtype", ["inner", "left"])
def test_une_jointure_qui_mene_le_filtre_reste_acceptee(both_sides: Bench, jtype: str):
    assert both_sides.compile(_join_incremental(jtype))


def test_un_deuxieme_build_ne_duplique_pas_la_jointure(both_sides: Bench):
    """La preuve par l'exécution : deux builds, sources inchangées."""
    _, first = both_sides.build(_join_incremental("left"), target="lignes_jointes")
    assert first == [(1, 1)]
    _, second = both_sides.build(_join_incremental("left"), target="lignes_jointes")
    assert second == [(1, 1)], "rien n'est arrivé : rien ne doit s'ajouter"

    # Et un vrai arrivage passe, une fois et une seule.
    both_sides.con.execute("insert into main.gauches values (2, 2)")
    _, third = both_sides.build(_join_incremental("left"), target="lignes_jointes")
    assert sorted(third) == [(1, 1), (2, 2)]


def test_la_colonne_incrementale_doit_venir_du_dataset_qui_mene(both_sides: Bench):
    """`max(ts)` est lu dans la cible, mais comparé dans la première entrée."""
    msg = compile_ko(
        both_sides,
        _join_incremental(
            "left",
            select=[
                {"from": "gauches", "column": "id"},
                {"from": "droites", "column": "ts"},
            ],
        ),
    )
    assert "ts" in msg and "gauches" in msg


def test_la_colonne_incrementale_renommee_est_refusee(both_sides: Bench):
    msg = compile_ko(
        both_sides,
        _join_incremental(
            "left",
            select=[
                {"from": "gauches", "column": "id"},
                {"from": "gauches", "column": "id", "as": "ts"},
            ],
        ),
    )
    assert "ts" in msg


def test_la_colonne_incrementale_doit_exister_sans_liste_de_selection(
    both_sides: Bench,
):
    msg = compile_ko(both_sides, _join_incremental("left", select=[], column="maj"))
    assert "maj" in msg and "gauches" in msg


# --------------------------------------------------------------- microbatch
#
# L'interface proposait « microbatch » sans jamais écrire ce que dbt exige :
# le modèle produit ne parsait pas. Ces contrôles rejouent les règles de dbt
# (`check_valid_microbatch_config`) pour les dire avant l'écriture.


def _microbatch_out(**cfg) -> dict:
    base = {"event_time": "vu_le", "begin": "2024-01-01", "batch_size": "day"}
    base.update(cfg)
    return {
        "materialized": "incremental",
        "incremental_strategy": "microbatch",
        "microbatch": base,
    }


def test_microbatch_ecrit_les_trois_reglages_que_dbt_exige():
    out = rcp.config_block(_microbatch_out())
    assert "incremental_strategy='microbatch'" in out
    assert "event_time='vu_le'" in out
    assert "begin='2024-01-01'" in out
    assert "batch_size='day'" in out


def test_microbatch_sans_colonne_de_temps_est_bloque():
    with pytest.raises(RecipeError, match="colonne de temps"):
        rcp.config_block(_microbatch_out(event_time=""))


def test_microbatch_sans_date_de_depart_est_bloque():
    with pytest.raises(RecipeError, match="date de départ"):
        rcp.config_block(_microbatch_out(begin=""))


def test_microbatch_date_de_depart_illisible_est_bloquee():
    with pytest.raises(RecipeError, match="Date de départ invalide"):
        rcp.config_block(_microbatch_out(begin="01/01/2024"))


def test_microbatch_taille_de_tranche_inconnue_est_bloquee():
    with pytest.raises(RecipeError, match="Taille de tranche"):
        rcp.config_block(_microbatch_out(batch_size="semaine"))


def test_microbatch_accepte_la_reprise_de_tranches():
    assert "lookback=3" in rcp.config_block(_microbatch_out(lookback=3))


def test_microbatch_refuse_une_reprise_de_tranches_illisible():
    with pytest.raises(RecipeError, match="Reprise de tranches"):
        rcp.config_block(_microbatch_out(lookback="trois"))


def test_microbatch_n_ecrit_pas_de_cle_unique():
    """dbt réécrit la tranche entière : rapprocher ligne à ligne n'a pas de sens."""
    out = rcp.config_block({**_microbatch_out(), "unique_key": ["id"]})
    assert "unique_key" not in out


def test_microbatch_n_ajoute_aucun_filtre_a_la_main():
    """C'est dbt qui borne, tranche par tranche.

    Un `where col > max(col)` écrit en plus viderait toute tranche de
    rattrapage : `max` porte sur la table entière et dépasse déjà la fenêtre
    de la tranche qu'on recalcule.
    """
    spec = {
        "name": "vues",
        "type": "prepare",
        "inputs": [{"ref": "brut", "alias": "brut"}],
        "output": {**_microbatch_out(), "incremental": {"column": "vu_le"}},
        "steps": [],
    }
    sql = rcp.compile_recipe(spec, {"brut": [{"name": "vu_le", "type": "TIMESTAMP"}]})
    assert "is_incremental()" not in sql
    assert "{{ this }}" not in sql


def test_microbatch_exige_que_la_colonne_de_temps_sorte_du_modele():
    spec = {
        "name": "vues",
        "type": "prepare",
        "inputs": [{"ref": "brut", "alias": "brut"}],
        "output": _microbatch_out(),
        "steps": [
            {
                "id": "s1",
                "enabled": True,
                "type": "keep_delete",
                "params": {"columns": ["id"], "action": "keep"},
            }
        ],
    }
    cols = {
        "brut": [{"name": "id", "type": "INTEGER"}, {"name": "vu_le", "type": "DATE"}]
    }
    with pytest.raises(RecipeError, match="ne sort pas de ce script"):
        rcp.compile_recipe(spec, cols)


def test_une_agregation_microbatch_doit_grouper_par_sa_colonne_de_temps():
    """Sinon un groupe à cheval sur deux tranches donne deux totaux partiels.

    dbt réécrit une tranche entière : le groupe est recalculé une fois par
    tranche, sur une moitié de ses lignes chaque fois, et les deux résultats
    cohabitent dans la table sans qu'aucun build n'échoue.
    """
    spec = {
        "name": "par_client",
        "type": "group",
        "inputs": [{"ref": "ventes", "alias": "ventes"}],
        "group_by": ["client"],
        "aggregations": [{"fn": "sum", "column": "montant", "alias": "total"}],
        "output": _microbatch_out(),
    }
    cols = {
        "ventes": [
            {"name": "client", "type": "VARCHAR"},
            {"name": "montant", "type": "DOUBLE"},
            {"name": "vu_le", "type": "DATE"},
        ]
    }
    with pytest.raises(RecipeError, match="colonne de temps"):
        rcp.compile_recipe(spec, cols)

    spec["group_by"] = ["client", "vu_le"]
    assert "batch_size='day'" in rcp.compile_recipe(spec, cols)


# ------------------------------------------------------ fenêtre de reprise


def _lookback_out(n, unit="day", strategy="delete+insert") -> dict:
    return {
        "materialized": "incremental",
        "incremental_strategy": strategy,
        "unique_key": ["id"],
        "incremental": {
            "column": "ts",
            "operator": "gt",
            "lookback": {"n": n, "unit": unit},
        },
    }


def test_la_fenetre_de_reprise_recule_la_borne():
    where = rcp.incremental_where(_lookback_out(3))
    assert "interval '3 day'" in where


def test_sans_fenetre_la_borne_reste_le_maximum_nu():
    assert "interval" not in rcp.incremental_where(_lookback_out(0))
    assert "interval" not in rcp.incremental_where(_lookback_out(""))


def test_une_fenetre_de_reprise_avec_append_est_refusee():
    """Relire suppose de remplacer : en `append`, la fenêtre fabrique des doublons."""
    with pytest.raises(RecipeError, match="append"):
        rcp.incremental_where(_lookback_out(3, strategy="append"))


def test_une_fenetre_de_reprise_negative_est_refusee():
    with pytest.raises(RecipeError, match="négative"):
        rcp.incremental_where(_lookback_out(-1))


def test_une_unite_de_reprise_inconnue_est_refusee():
    with pytest.raises(RecipeError, match="Unité de reprise"):
        rcp.incremental_where(_lookback_out(2, unit="semaine"))


def test_la_fenetre_de_reprise_suit_le_dialecte():
    """Reculer un horodatage ne s'écrit pas pareil d'un entrepôt à l'autre."""
    expected = {
        "duckdb": "interval '2 day'",
        "postgres": "interval '2 day'",
        "snowflake": "dateadd(day, -2,",
        "redshift": "dateadd(day, -2,",
        "athena": "date_add('day', -2,",
        "bigquery": "timestamp_sub(",
    }
    for adapter, chunk in expected.items():
        with rcp.using_dialect(adapter):
            where = rcp.incremental_where(_lookback_out(2), col_type="TIMESTAMP")
        assert chunk in where, adapter


def test_bigquery_refuse_une_fenetre_de_reprise_sur_un_type_inconnu():
    """Trois fonctions selon le type : parier donnerait une requête refusée."""
    with rcp.using_dialect("bigquery"):
        with pytest.raises(
            RecipeError, match="date_sub, datetime_sub ou timestamp_sub"
        ):
            rcp.incremental_where(_lookback_out(2), col_type=None)
        assert "date_sub(" in rcp.incremental_where(_lookback_out(2), col_type="DATE")


# ---------------------------------------- dédoublonnage, fenêtres, pivot


def _state(*name_types) -> ColumnState:
    cols = [n for n, _ in name_types]
    return ColumnState(cols, dict(name_types))


def _launch(processor: str, params: dict, state: ColumnState):
    return rcp.PROCESSORS[processor]["fn"](state, params)


def test_dedoublonner_par_cle_sans_cle_est_refuse():
    with pytest.raises(RecipeError, match="colonne de clé"):
        _launch("dedup_key", {"keys": []}, _state(("id", "INTEGER")))


def test_dedoublonner_par_cle_sans_tri_est_refuse():
    """Sans tri, la ligne gardée serait tirée au hasard — et pas la même demain."""
    with pytest.raises(RecipeError, match="sur quoi trier"):
        _launch("dedup_key", {"keys": ["id"]}, _state(("id", "INTEGER")))


def test_dedoublonner_par_cle_refuse_une_colonne_absente():
    with pytest.raises(RecipeError, match="n'existe pas"):
        _launch("dedup_key", {"keys": ["inconnue"]}, _state(("id", "INTEGER")))


def test_une_fonction_de_fenetre_de_position_exige_un_ordre():
    with pytest.raises(RecipeError, match="besoin d'un ordre"):
        _launch(
            "window_function",
            {"fn": "rank", "into": "rang", "partition_by": ["client"]},
            _state(("client", "VARCHAR")),
        )


def test_un_total_de_fenetre_refuse_un_ordre():
    """Avec un ordre, l'entrepôt en ferait un cumul, en silence."""
    with pytest.raises(RecipeError, match="tout ce qui précède"):
        _launch(
            "window_function",
            {
                "fn": "sum",
                "column": "montant",
                "into": "total",
                "order_by": [{"column": "jour"}],
            },
            _state(("montant", "DOUBLE"), ("jour", "DATE")),
        )


def test_une_moyenne_glissante_porte_sur_au_moins_deux_lignes():
    with pytest.raises(RecipeError, match="au moins deux lignes"):
        _launch(
            "window_function",
            {
                "fn": "moving_avg",
                "column": "montant",
                "into": "m",
                "order_by": [{"column": "jour"}],
                "window_rows": 1,
            },
            _state(("montant", "DOUBLE"), ("jour", "DATE")),
        )


def test_depivoter_refuse_des_colonnes_de_genres_differents():
    """Empilées dans une colonne, l'entrepôt refuserait — ou convertirait en silence."""
    with pytest.raises(RecipeError, match="même genre de valeur"):
        _launch(
            "unpivot",
            {"columns": ["jan", "libelle"]},
            _state(("jan", "DOUBLE"), ("libelle", "VARCHAR")),
        )


def test_depivoter_refuse_d_ecraser_une_colonne_conservee():
    with pytest.raises(RecipeError, match="déjà une colonne conservée"):
        _launch(
            "unpivot",
            {"columns": ["jan", "fev"], "name_into": "client"},
            _state(("client", "VARCHAR"), ("jan", "DOUBLE"), ("fev", "DOUBLE")),
        )


def test_pivoter_sans_valeurs_est_refuse():
    """Une requête ne peut pas inventer ses colonnes : la liste est donnée."""
    with pytest.raises(RecipeError, match="listez les valeurs"):
        _launch(
            "pivot",
            {"key_columns": ["client"], "name_column": "mois", "value_column": "ca"},
            _state(("client", "VARCHAR"), ("mois", "VARCHAR"), ("ca", "DOUBLE")),
        )


def test_pivoter_refuse_deux_colonnes_du_meme_nom():
    with pytest.raises(RecipeError, match="s'appelleraient"):
        _launch(
            "pivot",
            {
                "key_columns": ["client"],
                "name_column": "mois",
                "value_column": "ca",
                "values": ["jan", "JAN"],
                "prefix": "m_",
            },
            _state(("client", "VARCHAR"), ("mois", "VARCHAR"), ("ca", "DOUBLE")),
        )


def test_pivoter_refuse_une_valeur_qui_ne_fait_pas_un_nom():
    with pytest.raises(RecipeError, match="préfixe"):
        rcp.pivot_alias("2024")


def test_pivoter_accepte_la_meme_valeur_avec_un_prefixe():
    assert rcp.pivot_alias("2024", "an_") == "an_2024"


def test_une_fenetre_refuse_d_ecraser_la_colonne_dont_elle_se_sert():
    with pytest.raises(RecipeError, match="sert déjà au calcul"):
        _launch(
            "window_function",
            {
                "fn": "running_sum",
                "column": "montant",
                "into": "montant",
                "order_by": [{"column": "jour"}],
            },
            _state(("montant", "DOUBLE"), ("jour", "DATE")),
        )


def test_une_valeur_numerique_aberrante_ne_part_pas_nue_dans_le_sql():
    """`float()` accepte « inf » et « 1_000 » : l'entrepôt, non."""
    assert rcp.typed_lit("1000", "INTEGER") == "1000"
    assert rcp.typed_lit("1_000", "INTEGER") == "'1_000'"
    assert rcp.typed_lit("inf", "DOUBLE") == "'inf'"
    assert rcp.typed_lit("nan", "DOUBLE") == "'nan'"
    assert rcp.typed_lit("-1.5e3", "DOUBLE") == "-1.5e3"


# ------------------------------------------------- unicité des colonnes de sortie


def test_renommer_vers_un_nom_deja_pris_est_refuse(orders: Bench):
    """Le compilateur annonçait `['b', 'b']` là où DuckDB rendait `['b', 'b_1']`.

    L'état servi aux étapes suivantes ne décrivait donc plus le SQL exécuté :
    une formule posée ensuite sur ce nom relisait la première des deux
    colonnes, et la valeur de la seconde disparaissait sans un mot.
    """
    msg = compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "rename",
                    "params": {"renames": [{"from": "status", "to": "country"}]},
                }
            ]
        ),
    )
    assert "« country »" in msg and "s'appelleraient" in msg


def test_la_casse_seule_ne_suffit_pas_a_distinguer_deux_colonnes(orders: Bench):
    """Sur un identifiant nu, tous les entrepôts visés confondent `a` et `A`."""
    msg = compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "rename",
                    "params": {"renames": [{"from": "status", "to": "COUNTRY"}]},
                }
            ]
        ),
    )
    assert "s'appelleraient" in msg


def _group(aggs: list[dict], keys: list[str] | None = None) -> dict:
    return {
        "name": "g",
        "type": "group",
        "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
        "group_by": keys if keys is not None else ["customer_id"],
        "aggregations": aggs,
        "output": {},
    }


def test_deux_mesures_du_meme_nom_sont_refusees(orders: Bench):
    """Le même piège que le renommage, sur le chemin que « Grouper » emprunte.

    Deux mesures nommées `total` compilaient sans un mot : DuckDB rendait
    `total` et `total_1`, `output_columns()` annonçait `total` deux fois, et
    l'analyse d'impact assurait ensuite à l'aval que la colonne existait
    toujours — alors que la seconde mesure y avait changé de nom.
    """
    msg = compile_ko(
        orders,
        _group(
            [
                {"fn": "sum", "column": "amount_eur", "alias": "total"},
                {"fn": "count", "column": "*", "alias": "total"},
            ]
        ),
    )
    assert "« total »" in msg and "s'appelleraient" in msg


def test_une_mesure_ne_peut_pas_reprendre_le_nom_d_une_cle(orders: Bench):
    """La collision vaut entre les deux familles : elles sortent du même select."""
    msg = compile_ko(
        orders,
        _group([{"fn": "count", "column": "*", "alias": "customer_id"}]),
    )
    assert "« customer_id »" in msg and "s'appelleraient" in msg


def test_le_compilateur_de_group_annonce_ce_qu_il_ecrit(orders: Bench):
    """`output_columns()` et le SQL doivent dire la même chose, ou rien."""
    input_cols = {"stg_orders": [{"name": "customer_id", "type": "INTEGER"}]}
    valid = _group([{"fn": "count", "column": "*", "alias": "nb"}])
    assert rcp.output_columns(valid, input_cols) == ["customer_id", "nb"]

    fake = _group(
        [
            {"fn": "count", "column": "*", "alias": "nb"},
            {"fn": "sum", "column": "amount_eur", "alias": "nb"},
        ]
    )
    # Plutôt qu'un `['nb', 'nb']` que l'entrepôt ne rendra jamais.
    assert rcp.output_columns(fake, input_cols) is None


def test_un_echange_de_noms_reste_permis(orders: Bench):
    """`a → b` et `b → a` dans la même étape : chaque nom reste unique."""
    cols, rows = orders.run(
        prepare_spec(
            [
                {
                    "type": "rename",
                    "params": {
                        "renames": [
                            {"from": "status", "to": "country"},
                            {"from": "country", "to": "status"},
                        ]
                    },
                }
            ]
        )
    )
    assert cols.count("country") == 1 and cols.count("status") == 1
    assert rows[0][cols.index("country")] == "completed"


def test_une_colonne_ajoutee_ne_peut_pas_ecraser_une_existante(orders: Bench):
    """La vérification ne vise pas que le renommage : tout processeur qui ajoute
    une colonne doit rendre un nom libre."""
    msg = compile_ko(
        orders,
        prepare_spec(
            [
                {
                    "type": "unpivot",
                    "params": {
                        "columns": ["status", "country"],
                        "name_into": "order_id",
                        "value_into": "valeur",
                    },
                }
            ]
        ),
    )
    assert "order_id" in msg


def test_deux_colonnes_d_entree_que_seule_la_casse_separe_passent():
    """Seul un doublon *créé par l'étape* est refusé.

    BigQuery distingue `id` de `ID` ; DuckDB refuse même de créer une telle
    table, d'où la vérification directe plutôt qu'un aller-retour par l'entrepôt.
    Une entrée qui porte déjà les deux noms vient du projet, et ce n'est pas à
    une étape de filtrage de la rejeter.
    """
    rcp._check_unique_columns(["id", "ID"], ["id", "ID"], "Filtrer")

    with pytest.raises(RecipeError):
        rcp._check_unique_columns(["id", "ID", "x"], ["id", "ID", "ID"], "Renommer")


# ------------------------------------------- paramètres numériques d'étape


@pytest.mark.parametrize(
    "step",
    [
        {"type": "round", "params": {"column": "amount_eur", "decimals": "beaucoup"}},
        {"type": "round", "params": {"column": "amount_eur", "decimals": [1]}},
        {
            "type": "split_column",
            "params": {"column": "status", "separator": "-", "count": "deux"},
        },
        {
            "type": "split_column",
            "params": {"column": "status", "separator": "-", "count": 0},
        },
    ],
)
def test_un_parametre_numerique_mal_tape_est_refuse_proprement(orders, step):
    """`int()` à nu levait une `ValueError`, que les routes n'attrapent pas.

    L'aperçu, la compilation et l'enregistrement répondaient 500 sans un mot,
    là où toutes les autres étapes nomment le paramètre fautif. L'interface ne
    le produit pas — ses champs numériques passent par `parseInt(...) || 0` —
    mais un script visuel repris à la main dans `.pliq/recipes/` y arrive, et
    c'est un usage que le produit annonce.
    """
    msg = compile_ko(orders, prepare_spec([step]))
    assert "nombre entier" in msg or "au moins 1" in msg


def test_un_arrondi_negatif_reste_permis(orders):
    """`round(x, -2)` arrondit à la centaine : le signe n'est pas une erreur."""
    sql = orders.compile(
        prepare_spec(
            [{"type": "round", "params": {"column": "amount_eur", "decimals": -2}}]
        )
    )
    assert "round(" in sql.lower()


def test_un_parametre_numerique_absent_prend_son_defaut(orders):
    """Un paramètre qui manque n'est pas un paramètre faux."""
    sql = orders.compile(
        prepare_spec([{"type": "round", "params": {"column": "amount_eur"}}])
    )
    assert "round(" in sql.lower()


# ------------------------------- deux entrées ne peuvent pas porter le même nom


def test_deux_entrees_homonymes_sont_refusees_avec_leur_place():
    """Une autojointure produisait deux CTE `orders`, et l'entrepôt tranchait.

    L'interface prenait le nom du dataset comme alias sans vérifier qu'il était
    libre : `Duplicate CTE name "orders"` arrivait plusieurs écrans après le
    choix. Deux sources qui portent le même nom de table tombaient pareil.
    """
    with pytest.raises(RecipeError) as exc:
        rcp.input_aliases([{"ref": "orders"}, {"ref": "orders"}])
    message = str(exc.value)
    assert "n° 1" in message and "n° 2" in message
    assert "orders" in message


def test_deux_sources_du_meme_nom_de_table_aussi():
    with pytest.raises(RecipeError, match="s'appellent tous les deux"):
        rcp.input_aliases(
            [
                {"source_name": "brut", "table": "orders"},
                {"source_name": "legacy", "table": "orders"},
            ]
        )


def test_des_alias_distincts_passent():
    assert rcp.input_aliases(
        [{"ref": "orders", "alias": "orders"}, {"ref": "orders", "alias": "orders_2"}]
    ) == ["orders", "orders_2"]


def test_un_nom_dbt_qui_n_est_pas_un_identifiant_devient_un_alias_lisible():
    """`order-items` est une table de source valide ; un CTE, non.

    Refuser l'entrée revenait à refuser la source ; on rabat le nom sur un
    identifiant plutôt que de fermer la porte.
    """
    assert rcp.input_alias({"source_name": "brut", "table": "order-items"}, 0) == (
        "order_items"
    )
    assert rcp.input_alias({"table": "2024_sales"}, 0) == "_2024_sales"
    assert rcp.input_alias({}, 3) == "input_3"


def test_une_autojointure_compile_et_s_execute(orders):
    """Le cas banal que le constat a levé : le même dataset des deux côtés."""
    spec = {
        "name": "commandes_appairees",
        "type": "join",
        "inputs": [
            {"ref": "stg_orders", "alias": "stg_orders"},
            {"ref": "stg_orders", "alias": "stg_orders_2"},
        ],
        "joins": [
            {"type": "inner", "on": [{"left": "customer_id", "right": "customer_id"}]}
        ],
        "select": [
            {"from": "stg_orders", "column": "order_id"},
            {"from": "stg_orders_2", "column": "order_id", "as": "autre_order_id"},
        ],
        "output": {"materialized": "table"},
    }
    sql = orders.compile(spec)
    assert "stg_orders as (" in sql and "stg_orders_2 as (" in sql
    cols, rows = orders.run(spec)
    assert cols == ["order_id", "autre_order_id"]
    assert rows, "l'autojointure doit rendre des lignes, pas une erreur d'entrepôt"


# ------------------------------------------------- forme de l'enveloppe
#
# `spec: dict` à l'entrée de l'API accepte n'importe quel objet JSON. Les
# compilateurs, eux, parcourent `spec` comme un arbre de dicts et de listes :
# un `inputs: [null]` ne s'arrêtait qu'au premier `.get()` sur un `None`, en
# 500. Or l'atelier s'interdit de rendre une panne pour un refus.


@pytest.mark.parametrize(
    "spec",
    [
        None,
        [],
        "prepare",
        42,
        {"type": []},
        {"type": 3},
        {"type": "prepare", "name": 42},
        {"type": "prepare", "name": ["orders"]},
        {"type": "sql", "sql": 42},
        {"type": "prepare", "inputs": "orders"},
        {"type": "prepare", "inputs": {"ref": "orders"}},
        {"type": "prepare", "inputs": [None]},
        {"type": "prepare", "inputs": ["orders"]},
        {"type": "prepare", "inputs": [[]]},
        {"type": "prepare", "inputs": [{"ref": "o"}, None]},
        {"type": "prepare", "inputs": [{"ref": "o"}], "steps": "drop"},
        {"type": "prepare", "inputs": [{"ref": "o"}], "steps": [None]},
        {"type": "prepare", "inputs": [{"ref": "o"}], "steps": ["drop"]},
        {"type": "prepare", "inputs": [{"ref": "o"}], "output": "table"},
    ],
)
def test_une_enveloppe_malformee_est_refusee_et_non_une_panne(spec):
    with pytest.raises(RecipeError):
        rcp.check_spec_shape(spec)


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"type": None},
        {"type": "prepare"},
        # Un champ vide vaut un champ absent : c'est ce que disent les `or ""`
        # et `or []` du compilateur, et ce contrôle ne doit pas être plus
        # sévère qu'eux.
        {"type": "prepare", "name": None},
        {"type": "prepare", "name": ""},
        {"type": "prepare", "inputs": []},
        {"type": "prepare", "inputs": None},
        {"type": "prepare", "inputs": [{"ref": "o"}], "steps": []},
        {"type": "prepare", "inputs": [{"ref": "o"}], "output": {}},
        {"type": "prepare", "inputs": [{"ref": "o"}], "output": None},
    ],
)
def test_une_enveloppe_bien_formee_passe(spec):
    assert rcp.check_spec_shape(spec) is spec


def test_le_refus_dit_quel_element_est_en_cause():
    """Un message qui ne situe pas la faute ne sert à rien sur dix entrées."""
    with pytest.raises(RecipeError) as exc:
        rcp.check_spec_shape(
            {"type": "prepare", "inputs": [{"ref": "a"}, {"ref": "b"}, None]}
        )
    assert "n° 3" in str(exc.value) and "inputs" in str(exc.value)


def test_la_forme_ne_juge_pas_le_sens():
    """Une recipe structurellement saine mais absurde n'est pas son affaire.

    Le type inconnu, la jointure sans clé, la colonne absente appartiennent aux
    compilateurs : eux seuls savent en dire la raison.
    """
    rcp.check_spec_shape({"type": "type_qui_n_existe_pas", "inputs": [{}]})


# ------------------------------------------------- réglages d'étape


@pytest.mark.parametrize(
    "renames",
    [[None], ["order_id"], [{"from": "a", "to": "b"}, None], "order_id", {"a": "b"}],
)
def test_des_reglages_d_etape_malformes_sont_refuses_et_non_une_panne(renames):
    """`params` était jugé objet, et son contenu supposé bien formé.

    Le processeur de renommage lit `r.get("from")` sur chaque élément :
    `[null]` — ou `["colonne"]`, aussi facile à saisir à la main — arrivait
    jusque-là et rendait un 500.
    """
    with pytest.raises(RecipeError):
        rcp.check_spec_shape(
            {
                "type": "prepare",
                "inputs": [{"ref": "o"}],
                "steps": [{"type": "rename", "params": {"renames": renames}}],
            }
        )


@pytest.mark.parametrize(
    "params",
    [
        {"renames": [{"from": "a", "to": "b"}]},
        # Une liste vide vaut un champ absent : c'est le processeur qui dit
        # « aucune colonne indiquée », et il le dit mieux.
        {"renames": []},
        {"renames": None},
        {},
    ],
)
def test_des_reglages_d_etape_bien_formes_passent(params):
    spec = {
        "type": "prepare",
        "inputs": [{"ref": "o"}],
        "steps": [{"type": "rename", "params": params}],
    }
    assert rcp.check_spec_shape(spec) is spec


def test_les_listes_de_noms_ne_sont_pas_jugees_ici():
    """`columns` est une liste de colonnes : un intrus y reste l'affaire de l'étape.

    Le processeur répond « colonne inconnue » en nommant l'étape, ce que la
    charpente ne saurait pas faire.
    """
    rcp.check_spec_shape(
        {
            "type": "prepare",
            "inputs": [{"ref": "o"}],
            "steps": [{"type": "keep_delete", "params": {"columns": [None, {}]}}],
        }
    )


def _object_lists_read_by_processors() -> dict[str, set[str]]:
    """Les clés de `params` que `processors.py` parcourt comme des objets.

    Relire le source plutôt que de faire confiance à une liste écrite à la
    main : c'est ce qui empêche `OBJECT_LIST_PARAMS` de dériver quand un
    processeur arrive. La lecture est délibérément conservatrice — elle ne
    reconnaît que `for x in <ce qui vient de params>` suivi d'un `x.get(...)` —
    et une clé qu'elle raterait laisse simplement le contrôle en place, jamais
    un test qui échoue à tort.
    """
    import ast
    import inspect

    from pliq.recipes import processors as mod

    tree = ast.parse(inspect.getsource(mod))

    def param_keys(node) -> set[str]:
        return {
            under.args[0].value
            for under in ast.walk(node)
            if isinstance(under, ast.Call)
            and isinstance(under.func, ast.Attribute)
            and under.func.attr == "get"
            and isinstance(under.func.value, ast.Name)
            and under.func.value.id == "params"
            and under.args
            and isinstance(under.args[0], ast.Constant)
        }

    def calls_get_on(body, name: str) -> bool:
        return any(
            isinstance(under, ast.Attribute)
            and under.attr == "get"
            and isinstance(under.value, ast.Name)
            and under.value.id == name
            for under in ast.walk(body)
        )

    read_lists: dict[str, set[str]] = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        # La clé du processeur est le premier argument de son décorateur.
        key = next(
            (
                d.args[0].value
                for d in fn.decorator_list
                if isinstance(d, ast.Call)
                and isinstance(d.func, ast.Name)
                and d.func.id == "processor"
                and d.args
                and isinstance(d.args[0], ast.Constant)
            ),
            None,
        )
        if key is None:
            continue
        # Les variables qui viennent de `params`, pour suivre `x = params.get(...)`
        # puis `for item in x:`.
        origin: dict[str, set[str]] = {}
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and (coming_from := param_keys(node.value))
            ):
                origin[node.targets[0].id] = coming_from

        # `origin` est lié à la définition : la fonction vit dans la boucle, et
        # sans ça elle lirait le dictionnaire de la *dernière* itération.
        def source(iterable, origin=origin) -> set[str]:
            if isinstance(iterable, ast.Name):
                return origin.get(iterable.id, set())
            return param_keys(iterable)

        found_entries: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                if (coming_from := source(node.iter)) and any(
                    calls_get_on(stmt, node.target.id) for stmt in node.body
                ):
                    found_entries |= coming_from
            if isinstance(
                node, (ast.ListComp, ast.GeneratorExp, ast.DictComp, ast.SetComp)
            ):
                for gen in node.generators:
                    if (
                        isinstance(gen.target, ast.Name)
                        and (coming_from := source(gen.iter))
                        and calls_get_on(node, gen.target.id)
                    ):
                        found_entries |= coming_from
        if found_entries:
            read_lists[key] = found_entries
    return read_lists


def test_aucune_liste_d_objets_de_params_n_echappe_au_controle():
    """Un processeur qui lit une liste d'objets doit la déclarer.

    Sans ce contrôle, `OBJECT_LIST_PARAMS` est une liste écrite à la main
    à côté du code qu'elle décrit : le prochain processeur qui parcourt une
    liste d'objets rouvrirait la panne en silence. Le remède est d'ajouter la
    clé au vocabulaire, pas de modifier ce test.
    """
    from pliq.recipes.vocabulary import OBJECT_LIST_PARAMS

    read_lists = _object_lists_read_by_processors()
    assert (
        read_lists
    ), "la relecture de processors.py ne trouve plus rien : elle a cassé"
    missing = {
        key: sorted(fields - set(OBJECT_LIST_PARAMS.get(key, ())))
        for key, fields in read_lists.items()
        if fields - set(OBJECT_LIST_PARAMS.get(key, ()))
    }
    assert not missing, (
        "ces réglages sont parcourus comme des objets sans être déclarés dans "
        f"PARAMS_LISTES_D_OBJETS : {missing}"
    )

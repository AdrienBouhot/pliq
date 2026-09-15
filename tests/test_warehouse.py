"""Lecture de l'entrepôt, entièrement via `dbt show`.

Deux niveaux : la conversion agate → JSON se teste sans dbt, et un test marqué
`dbt` vérifie que le chemin complet marche pour de vrai sur un projet construit.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
from types import SimpleNamespace

import duckdb
import pytest

from pliq.config import Settings
from pliq.dbt_service import DbtService, ShowError
from pliq.warehouse import (
    _INVENTORY_SQL,
    Warehouse,
    WarehouseError,
    _cell,
    _readable,
    q,
)

# --------------------------------------------------------------- faux agate


class FakeType:
    """Imite un type agate : seul son nom de classe compte."""


def agate_table(columns: list[tuple[str, str]], rows: list[list]):
    types = []
    for _, type_name in columns:
        types.append(type(type_name, (FakeType,), {})())
    return SimpleNamespace(
        column_names=tuple(name for name, _ in columns),
        column_types=tuple(types),
        rows=rows,
    )


class FakeService:
    """Un DbtService qui rend ce qu'on lui dit, et note ce qu'on lui demande."""

    def __init__(self, table=None, error: Exception | None = None):
        self.table = table
        self.error = error
        self.calls: list[tuple[str, int]] = []
        # Comme le vrai service : chaque parse réussi l'incrémente, et c'est ce
        # qui périme les colonnes que l'entrepôt garde en mémoire.
        self.generation = 0

    def show(self, sql: str, limit: int = 100):
        self.calls.append((sql, limit))
        if self.error is not None:
            raise self.error
        return self.table


def wh(table=None, error=None) -> tuple[Warehouse, FakeService]:
    svc = FakeService(table, error)
    return Warehouse(SimpleNamespace(), svc), svc


# ------------------------------------------------------------- la conversion


def test_les_types_agate_deviennent_des_noms_sql_lisibles():
    """profiling.storage_of raisonne sur des noms SQL, pas sur des noms agate."""
    workshop, _ = wh(
        agate_table(
            [
                ("n", "Integer"),
                ("nom", "Text"),
                ("prix", "Number"),
                ("actif", "Boolean"),
                ("jour", "Date"),
                ("t", "DateTime"),
            ],
            [
                [
                    1,
                    "a",
                    decimal.Decimal("2.5"),
                    True,
                    dt.date(2024, 1, 1),
                    dt.datetime(2024, 1, 1, 9, 30),
                ]
            ],
        )
    )
    data = workshop.query("select 1")
    assert [c["type"] for c in data["columns"]] == [
        "INTEGER",
        "VARCHAR",
        "DECIMAL",
        "BOOLEAN",
        "DATE",
        "TIMESTAMP",
    ]


def test_un_type_agate_inconnu_passe_en_clair_plutot_que_de_disparaitre():
    workshop, _ = wh(agate_table([("x", "Etrange")], [[None]]))
    assert workshop.query("select 1")["columns"][0]["type"] == "ETRANGE"


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        (True, True),
        (3, 3),
        ("texte", "texte"),
        (2.5, 2.5),
        (decimal.Decimal("430.50"), 430.5),
        (decimal.Decimal("12"), 12),
        (dt.date(2024, 3, 1), "2024-03-01"),
        (dt.datetime(2024, 3, 1, 14, 5), "2024-03-01T14:05:00"),
        (dt.timedelta(hours=2), "2:00:00"),
        (b"\xff\x00", "ff00"),
    ],
)
def test_chaque_valeur_devient_serialisable_en_json(value, expected):
    assert _cell(value) == expected


# ------------------------------------------------------------------ requêtes


def test_le_jinja_part_tel_quel_vers_dbt():
    """C'est tout l'intérêt : plus de résolution maison des ref()."""
    workshop, svc = wh(agate_table([("x", "Integer")], [[1]]))
    workshop.query("select * from {{ ref('stg_orders') }}", limit=7)
    assert svc.calls == [("select * from {{ ref('stg_orders') }}", 7)]


def test_un_echec_de_dbt_devient_une_erreur_d_entrepot():
    workshop, _ = wh(error=ShowError("Runtime Error\n  Table inconnue : bidule"))
    with pytest.raises(WarehouseError, match="Table inconnue"):
        workshop.query("select * from bidule")


def test_un_dataset_sans_relation_renvoie_vers_dbt_build():
    workshop, svc = wh()
    with pytest.raises(WarehouseError, match="dbt build"):
        workshop.preview_relation("")
    assert svc.calls == [], "inutile de déranger dbt pour ça"


def test_les_colonnes_seules_ne_rapatrient_pas_les_lignes():
    """Les types sont demandés à l'adaptateur ; l'échantillon est le repli.

    Le faux service rend la même table quoi qu'on lui demande : la réponse de
    l'adaptateur n'a donc pas la forme attendue, et c'est le repli qui répond —
    sur une ligne, comme avant.
    """
    workshop, svc = wh(agate_table([("a", "Text")], [["x"]]))
    assert workshop.columns_of_sql("select * from t") == [
        {"name": "a", "type": "VARCHAR"}
    ]
    assert "get_column_schema_from_query" in svc.calls[0][0]
    assert svc.calls[-1][1] == 1


def test_les_colonnes_ne_se_redemandent_pas_a_chaque_apercu():
    """Un aperçu relit les colonnes de chaque entrée à chaque frappe.

    Chacune est un aller-retour `dbt show` complet : une jointure à deux
    entrées en payait trois par aperçu, et rien de tout cela ne change tant
    que le projet n'est pas reparsé.
    """
    workshop, svc = wh(agate_table([("a", "Text")], [["x"]]))
    expected = [{"name": "a", "type": "VARCHAR"}]

    assert workshop.columns_of_sql("select * from t") == expected
    after_the_first = len(svc.calls)
    assert after_the_first > 0

    for _ in range(5):
        assert workshop.columns_of_sql("select * from t") == expected
    assert len(svc.calls) == after_the_first, "dbt redemandé pour rien"

    # Une autre requête n'est pas la même : elle se lit pour de bon.
    workshop.columns_of_sql("select * from autre")
    assert len(svc.calls) > after_the_first


def test_un_reparse_perime_les_colonnes_gardees():
    """Un enregistrement reparse : ce qu'on avait lu avant ne vaut plus.

    C'est tout l'intérêt de compter les parses plutôt que les secondes — un
    `ref()` qui change de cible n'attend pas la fin d'un délai.
    """
    workshop, svc = wh(agate_table([("a", "Text")], [["x"]]))
    workshop.columns_of_sql("select * from t")
    before = len(svc.calls)

    workshop.columns_of_sql("select * from t")
    assert len(svc.calls) == before

    svc.generation += 1  # ce que fait un parse réussi
    workshop.columns_of_sql("select * from t")
    assert len(svc.calls) > before


def test_les_colonnes_gardees_ne_se_laissent_pas_modifier():
    """L'appelant range ces dictionnaires dans un état de colonnes.

    Rendre l'objet du cache ferait qu'une modification chez lui vaudrait pour
    tous les aperçus suivants — et le compilateur écrirait du SQL d'après un
    type que personne n'a jamais lu.
    """
    workshop, _ = wh(agate_table([("a", "Text")], [["x"]]))
    first = workshop.columns_of_sql("select * from t")
    first[0]["type"] = "N'IMPORTE QUOI"
    assert workshop.columns_of_sql("select * from t") == [
        {"name": "a", "type": "VARCHAR"}
    ]


def test_le_compte_de_lignes_passe_par_dbt():
    workshop, svc = wh(agate_table([("n", "Integer")], [[42]]))
    assert workshop.count('"dev"."main"."t"') == 42
    assert "count(*)" in svc.calls[0][0]


def test_un_compte_impossible_ne_casse_pas_l_apercu():
    """Le total est un agrément : s'il échoue, l'aperçu doit rester affichable."""
    workshop, _ = wh(error=ShowError("boom"))
    assert workshop.count("main.t") is None
    workshop, svc = wh()
    assert workshop.count("") is None and svc.calls == []


# ---------------------------------------------------------------- inventaire


def test_l_inventaire_lit_information_schema():
    workshop, svc = wh(
        agate_table(
            [
                ("table_catalog", "Text"),
                ("table_schema", "Text"),
                ("table_name", "Text"),
                ("table_type", "Text"),
                ("n_columns", "Integer"),
            ],
            [
                ["dev", "staging", "stg_orders", "BASE TABLE", 4],
                ["dev", "staging", "v_orders", "VIEW", 4],
            ],
        )
    )
    data = workshop.tables()
    assert "information_schema.tables" in svc.calls[0][0]
    assert data["database"] == "dev"
    assert [(t["name"], t["type"], t["columns"]) for t in data["tables"]] == [
        ("stg_orders", "table", 4),
        ("v_orders", "view", 4),
    ]
    assert all(
        t["rows"] is None for t in data["tables"]
    ), "le nombre de lignes n'a pas d'équivalent portable : on ne l'invente pas"


def test_l_inventaire_survit_a_un_entrepot_qui_nomme_ses_colonnes_autrement():
    """Certains adaptateurs renvoient les colonnes en majuscules."""
    workshop, _ = wh(
        agate_table(
            [
                ("TABLE_CATALOG", "Text"),
                ("TABLE_SCHEMA", "Text"),
                ("TABLE_NAME", "Text"),
                ("TABLE_TYPE", "Text"),
                ("N_COLUMNS", "Integer"),
            ],
            [["DB", "PUBLIC", "CLIENTS", "BASE TABLE", 2]],
        )
    )
    t = workshop.tables()["tables"][0]
    assert (t["schema"], t["name"], t["columns"]) == ("PUBLIC", "CLIENTS", 2)


def test_l_inventaire_ne_confond_pas_deux_catalogues(tmp_path):
    """Deux bases attachées, une table du même nom dans chacune.

    La requête joignait `tables` et `columns` sur le schéma et le nom seuls :
    les colonnes des deux tables s'additionnaient, et l'inventaire annonçait
    trois colonnes de chaque côté. On exécute la vraie requête sur un vrai
    DuckDB — c'est le seul moyen honnête de vérifier une jointure.
    """
    con = duckdb.connect(":memory:")
    con.execute(f"attach '{tmp_path / 'deux.duckdb'}' as db_two")
    con.execute("create table memory.main.orders (id integer)")
    con.execute("create table db_two.main.orders (id integer, montant double)")

    tally = {
        (catalog_data, name): n
        for catalog_data, _schema, name, _type, n in con.execute(
            _INVENTORY_SQL
        ).fetchall()
    }
    con.close()

    assert tally[("memory", "orders")] == 1
    assert tally[("db_two", "orders")] == 2


# ------------------------------------------------------------------ messages


def test_le_message_d_erreur_garde_la_ligne_utile():
    raw_text = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "Catalog Error: Table with name absente does not exist!"
    )
    assert (
        _readable(raw_text) == "Catalog Error: Table with name absente does not exist!"
    )


def test_un_message_vide_reste_comprehensible():
    assert "entrepôt" in _readable("   \n  ")


def test_les_identifiants_sont_cites_au_standard_sql():
    assert q("schema") == '"schema"'
    assert q('a"b') == '"a""b"'


# ------------------------------------------------------------- bout en bout


@pytest.mark.dbt
def test_le_chemin_complet_marche_sur_un_vrai_projet(built_project: Settings):
    """Aucune connexion ouverte par Pliq : tout passe par l'adaptateur du projet."""
    svc = DbtService(built_project)
    workshop = Warehouse(built_project, svc)

    # Jinja compilé par dbt, pas par nous.
    data = workshop.query("select * from {{ ref('stg_orders') }}")
    assert [c["name"] for c in data["columns"]] == ["order_id", "status"]
    assert len(data["rows"]) == 2

    # Une macro : impossible avec l'ancienne résolution par regex.
    macro = workshop.query("select {{ 1 + 1 }} as deux")
    assert macro["rows"] == [[2]]

    # L'inventaire voit la table et la vue.
    names = {t["name"]: t for t in workshop.tables()["tables"]}
    assert names["stg_orders"]["type"] == "table"
    assert names["stg_orders"]["columns"] == 2

    assert workshop.count('"dev"."main"."stg_orders"') == 2


@pytest.mark.dbt
def test_une_relation_inconnue_donne_un_message_court(built_project: Settings):
    workshop = Warehouse(built_project, DbtService(built_project))
    with pytest.raises(WarehouseError) as exc:
        workshop.preview_relation("main.absente")
    message = str(exc.value)
    assert "absente does not exist" in message, "le diagnostic, pas sa périphérie"
    assert "Traceback" not in message and 'File "' not in message
    assert len(message.splitlines()) <= 5, "on ne montre pas une trace dbt"


# ----------------------- régression : un Decimal ne doit pas changer de valeur


@pytest.mark.parametrize(
    "value, expected",
    [
        # Au-delà de 2^53, ni float ni Number JavaScript ne sont exacts : on
        # rend le texte plutôt qu'un entier faux. Un identifiant NUMERIC(38,0)
        # de Snowflake tombe précisément dans ce cas.
        (decimal.Decimal("9007199254740993"), "9007199254740993"),
        (decimal.Decimal("-9007199254740993"), "-9007199254740993"),
        (
            decimal.Decimal("123456789012345678901234567890.5"),
            "123456789012345678901234567890.5",
        ),
        # En deçà, rien ne change : les entiers restent des entiers…
        (decimal.Decimal("9007199254740991"), 9007199254740991),
        (decimal.Decimal("0"), 0),
        # …et les montants restent des nombres, pas du texte.
        (decimal.Decimal("430.50"), 430.5),
        (decimal.Decimal("1.005"), 1.005),
    ],
)
def test_un_decimal_hors_de_portee_du_float_reste_exact(value, expected):
    rendered = _cell(value)
    assert rendered == expected
    assert type(rendered) is type(expected)


def test_un_decimal_non_fini_ne_casse_pas_la_serialisation():
    assert _cell(decimal.Decimal("NaN")) == "NaN"
    assert _cell(decimal.Decimal("Infinity")) == "Infinity"


# ------------------- régression : un entier natif non plus (même cause)


@pytest.mark.parametrize(
    "value, expected",
    [
        # La protection existait pour les Decimal seulement. Un BIGINT arrive
        # pourtant en `int` Python, et sortait tel quel : c'est `JSON.parse`
        # dans le navigateur qui l'abîmait, sans que rien ne le signale.
        (9007199254740993, "9007199254740993"),
        (-9007199254740993, "-9007199254740993"),
        (2**63 - 1, "9223372036854775807"),
        # En deçà de 2^53, un entier reste un entier.
        (9007199254740991, 9007199254740991),
        (0, 0),
        (-42, -42),
    ],
)
def test_un_entier_hors_de_portee_du_javascript_reste_exact(value, expected):
    rendered = _cell(value)
    assert rendered == expected
    assert type(rendered) is type(expected)


def test_un_booleen_reste_un_booleen():
    """`bool` est un `int` en Python : la règle des entiers ne doit pas le voir.

    Sans cette précaution, la case à cocher de l'aperçu afficherait « True ».
    """
    for value in (True, False):
        assert _cell(value) is value


def test_le_trajet_entrepot_vers_javascript_ne_perd_pas_d_identifiant():
    """Le test qui compte : ce n'est pas `_cell` qui abîmait la valeur, c'est JSON.

    Deux identifiants distincts au-delà de 2^53 devenaient une seule et même
    valeur une fois relus par le navigateur. On refait donc le trajet entier —
    conversion, sérialisation, relecture — plutôt que de s'arrêter à `_cell`.
    """
    workshop, _ = wh(
        agate_table([("id", "Integer")], [[9007199254740993], [9007199254740992]])
    )
    reread = json.loads(json.dumps(workshop.query("select id from t")["rows"]))
    assert reread == [["9007199254740993"], ["9007199254740992"]]
    assert reread[0] != reread[1], "deux identifiants distincts doivent le rester"


# ------------------------- les types viennent de l'entrepôt, pas du hasard (C2)


def test_une_colonne_dont_l_echantillon_est_nul_n_a_pas_de_type_invente():
    """agate typait une colonne VARCHAR en INTEGER, et le filtre suivait.

    `columns_of_sql` ne lit qu'une ligne. Quand cette ligne est NULL, le
    testeur de types de dbt n'a rien à regarder et rend son premier type —
    `Integer`. Sur une colonne de codes `['00123', '123']`, le filtre texte
    « 123 » s'écrivait alors `code = 123`, que DuckDB évalue en castant : les
    deux chaînes passaient. Ne pas savoir doit se dire.
    """
    workshop, _ = wh(agate_table([("code", "Integer"), ("n", "Integer")], [[None, 7]]))
    columns = workshop._columns_from_sample("select * from t")
    assert columns[0] == {"name": "code", "type": ""}
    assert columns[1] == {"name": "n", "type": "INTEGER"}


def test_un_type_inconnu_fait_citer_la_valeur_du_filtre():
    """Le revers du constat : sans type, on quote — c'est le côté sûr.

    Un entrepôt relit `'123'` sur une colonne numérique ; l'inverse comparait
    un texte à un nombre, et ramenait des lignes qui n'avaient rien à faire là.
    """
    from pliq.recipes import typed_lit

    assert typed_lit("123", "") == "'123'"
    assert typed_lit("123", None) == "'123'"
    assert typed_lit("123", "INTEGER") == "123"


def test_sans_ligne_du_tout_aucun_type_n_est_devine():
    workshop, _ = wh(agate_table([("a", "Integer")], []))
    assert workshop._columns_from_sample("select * from t") == [
        {"name": "a", "type": ""}
    ]


@pytest.mark.dbt
def test_le_type_d_une_colonne_vient_de_l_adaptateur(built_project: Settings):
    """Le vrai chemin : c'est le curseur qui dit VARCHAR, pas l'échantillon.

    `status` vaut NULL sur la ligne que `--limit 1` rapatrie ; l'inférence la
    typait donc en INTEGER. L'adaptateur, lui, sait de quelle colonne il parle.
    """
    workshop = Warehouse(built_project, DbtService(built_project))
    types = {
        c["name"]: c["type"]
        for c in workshop.columns_of_sql(
            "select order_id, status from {{ ref('stg_orders') }} where status is null"
        )
    }
    assert types == {"order_id": "INTEGER", "status": "VARCHAR"}


# --------------------------- un message d'erreur reste un message (C6)


def test_le_message_garde_le_diagnostic_et_pas_la_fleche():
    """`_readable` gardait la dernière ligne non vide : « ^ », et rien d'autre.

    L'utilisateur recevait la flèche sans l'erreur qu'elle pointe. Le
    diagnostic est ce qui précède le contexte, et le contexte se garde avec.
    """
    raw_text = (
        "DbtRuntimeError: Runtime Error\n"
        "  Runtime Error in sql_operation inline_query\n"
        '    Parser Error: syntax error at or near "slect"\n'
        "\n"
        '    LINE 4:   slect * from "dev"."main"."codes"\n'
        "              ^"
    )
    rendered = _readable(raw_text)
    assert rendered.splitlines()[0] == 'Parser Error: syntax error at or near "slect"'
    assert 'LINE 4:   slect * from "dev"."main"."codes"' in rendered
    assert rendered.endswith("^"), "la flèche reste, mais elle ne reste pas seule"
    assert "sql_operation" not in rendered, "la plomberie de dbt ne dit rien à personne"


def test_un_diagnostic_sur_deux_lignes_reste_entier():
    """« Catalog Error… » puis « Did you mean… » : la suggestion sans l'erreur
    ne vaut pas mieux que la flèche sans le diagnostic."""
    raw_text = (
        "DbtRuntimeError: Runtime Error\n"
        "  Runtime Error in sql_operation inline_query\n"
        "    Catalog Error: Table with name absente does not exist!\n"
        '    Did you mean "duckdb_types"?\n'
        "\n"
        "    LINE 4:   select * from main.absente\n"
        "                            ^"
    )
    rendered = _readable(raw_text).splitlines()
    assert rendered[0].startswith("Catalog Error:")
    assert rendered[1].startswith("Did you mean")
    assert rendered[-1].strip() == "^"


def test_la_fleche_reste_alignee_sous_ce_qu_elle_pointe():
    """Désindenter ligne à ligne aurait fait pointer la flèche à côté."""
    raw_text = "    Parser Error: oups\n    LINE 1: select x\n                   ^"
    lines = _readable(raw_text).splitlines()
    assert lines[1] == "LINE 1: select x"
    assert lines[2].index("^") == lines[1].index("x")


def _adapter_response(n: int):
    return agate_table(
        [("pliq_pos", "Integer"), ("pliq_nom", "Text"), ("pliq_type", "Text")],
        [[i + 1, f"c{i}", "VARCHAR"] for i in range(n)],
    )


def test_une_reponse_tronquee_de_l_adaptateur_passe_la_main_au_repli():
    """La limite borne les lignes, et il y en a une par colonne.

    Au-delà, les dernières colonnes manquent. Rendre la liste amputée ferait
    disparaître ces colonnes de l'atelier — sélecteurs et compilation comprises
    — alors que le repli, qui lit la requête elle-même, les voit toutes, quitte
    à ne pas savoir les typer.
    """
    from pliq.warehouse import _MAX_COLUMNS

    workshop, _ = wh(_adapter_response(_MAX_COLUMNS + 1))
    assert workshop._columns_from_adapter("select * from large") is None


def test_une_reponse_pile_a_la_borne_garde_ses_vrais_types():
    """On demande une ligne de plus que le maximum, justement pour distinguer.

    Sans cette marge, une requête à exactement `_MAX_COLUMNS` colonnes était
    indiscernable d'une réponse coupée, et retombait sur le repli — donc sur le
    typage approximatif que ce chemin est là pour remplacer.
    """
    from pliq.warehouse import _MAX_COLUMNS

    workshop, svc = wh(_adapter_response(_MAX_COLUMNS))
    columns = workshop._columns_from_adapter("select * from large")
    assert columns is not None and len(columns) == _MAX_COLUMNS
    assert svc.calls[0][1] == _MAX_COLUMNS + 1


def test_une_reponse_complete_de_l_adaptateur_est_rendue_dans_l_ordre():
    """`union all` ne promet aucun ordre : la position voyage avec la ligne."""
    response = agate_table(
        [("pliq_pos", "Integer"), ("pliq_nom", "Text"), ("pliq_type", "Text")],
        [[2, "b", "integer"], [1, "a", "varchar"]],
    )
    workshop, _ = wh(response)
    assert workshop._columns_from_adapter("select * from t") == [
        {"name": "a", "type": "VARCHAR"},
        {"name": "b", "type": "INTEGER"},
    ]

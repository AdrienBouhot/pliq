"""Le SQL produit doit être celui de l'entrepôt du projet, pas celui de DuckDB.

L'atelier ne parle jamais à l'entrepôt en direct : il écrit un `.sql` que dbt
enverra. Rien ne vérifiait donc, avant le `dbt build`, que ce fichier soit
lisible ailleurs que sur DuckDB — « Parser une date » sortait le même
`try_strptime(...)` pour Athena, Redshift et BigQuery, qui n'en ont aucun.

Ces tests-ci ne lancent aucun entrepôt distant : ils lisent le SQL produit, et
le font relire par sqlglot dans le dialecte visé. Ça ne prouve pas qu'une
requête rende les bonnes lignes sur Athena — seule une exécution le prouverait,
et c'est ce que fait `test_recipes_execution.py` pour DuckDB. Ça prouve que les
fonctions employées sont celles de l'entrepôt, ce qui est précisément ce qui
manquait.
"""

from __future__ import annotations

import re

import pytest
import sqlglot

from pliq import recipes as rcp

from .conftest import prepare_spec

# Les quatre entrepôts visés, plus les deux autres que l'atelier sait écrire.
WAREHOUSES = ["duckdb", "athena", "redshift", "bigquery", "postgres", "snowflake"]

COLUMNS = {
    "evts": [
        {"name": "d", "type": "VARCHAR"},
        {"name": "libelle", "type": "VARCHAR"},
        {"name": "montant", "type": "DOUBLE"},
        {"name": "client", "type": "VARCHAR"},
        {"name": "statut", "type": "VARCHAR"},
    ]
}


@pytest.fixture
def compile_on():
    """Compile une recipe en se plaçant sur l'entrepôt demandé."""

    def compiler(warehouse: str, spec: dict) -> str:
        # Borné : sans ça, le dernier entrepôt compilé restait réglé pour le
        # test suivant, qui croyait pourtant écrire pour DuckDB.
        with rcp.using_dialect(warehouse):
            return rcp.compile_recipe(spec, COLUMNS)

    return compiler


_JINJA_BLOCK = re.compile(r"\{%[\s\S]*?%\}")
_JINJA_CONFIG = re.compile(r"\{\{\s*config\([\s\S]*?\)\s*\}\}")
_JINJA_EXPR = re.compile(r"\{\{[\s\S]*?\}\}")


def reread(sql: str, warehouse: str) -> str:
    """Le SQL sans son Jinja, relu par sqlglot dans le dialecte de l'entrepôt.

    Les balises `{% if is_incremental() %}` disparaissent mais leur corps
    reste : c'est le deuxième build qu'on veut donner à relire, celui où le
    filtre incrémental est là.
    """
    body = _JINJA_BLOCK.sub("", sql)
    body = _JINJA_CONFIG.sub("", body)
    body = _JINJA_EXPR.sub("relation", body)
    # Le bandeau de tête n'est que du commentaire : sqlglot bute dessus quand
    # il précède le `with`, et il n'apprend rien sur la portabilité.
    lines = body.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
        lines.pop(0)
    body = "\n".join(lines)
    sqlglot.parse_one(body, dialect=warehouse)  # lève si l'entrepôt ne lit pas
    return body


def step(type_: str, **params) -> dict:
    return {"type": type_, "params": params}


# ------------------------------------------------------------------- dates


EXPECTED_PARSE = {
    "duckdb": "try_strptime(cast(d as varchar), '%d/%m/%Y')",
    "athena": "try(date_parse(cast(d as varchar), '%d/%m/%Y'))",
    "redshift": "to_timestamp(cast(d as varchar(max)), 'DD/MM/YYYY')",
    "bigquery": "safe.parse_timestamp('%d/%m/%Y', cast(d as string))",
    "postgres": "to_timestamp(cast(d as varchar), 'DD/MM/YYYY')",
    "snowflake": "try_to_timestamp(cast(d as varchar), 'DD/mm/yyyy')",
}


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_parser_une_date_parle_la_langue_de_l_entrepot(compile_on, warehouse: str):
    """`try_strptime` est du DuckDB : BigQuery veut `parse_timestamp`, et le
    format lui-même change — `%d/%m/%Y` devient `DD/MM/YYYY` sur Redshift."""
    sql = compile_on(
        warehouse,
        prepare_spec(
            [step("parse_date", column="d", format="%d/%m/%Y", into="jour")],
            ref="evts",
        ),
    )
    assert EXPECTED_PARSE[warehouse] in sql
    reread(sql, warehouse)


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_parser_sans_format_prend_le_cast_sûr_de_l_entrepot(compile_on, warehouse: str):
    sql = compile_on(
        warehouse, prepare_spec([step("parse_date", column="d")], ref="evts")
    )
    expected = {
        "bigquery": "safe_cast(d as date)",
        # Ni Redshift ni PostgreSQL n'ont de cast qui rende null : le modèle
        # échouera sur une valeur illisible, et l'aide de l'étape le dit.
        "redshift": "cast(d as date)",
        "postgres": "cast(d as date)",
    }.get(warehouse, "try_cast(d as date)")
    assert expected in sql
    reread(sql, warehouse)


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_les_composants_de_date_passent_par_extract(compile_on, warehouse: str):
    """`year(x)` n'existe ni sur Redshift, ni sur PostgreSQL, ni sur BigQuery.

    `extract(year from x)`, lui, est du SQL standard. Seul le nom du jour de la
    semaine change d'un entrepôt à l'autre.
    """
    sql = compile_on(
        warehouse,
        prepare_spec(
            [step("extract_date_parts", column="d", parts=["year", "dow"])], ref="evts"
        ),
    )
    assert "extract(year from d)" in sql
    day = {
        "athena": "day_of_week",
        "bigquery": "dayofweek",
        "snowflake": "dayofweek",
    }.get(warehouse, "dow")
    assert f"extract({day} from d)" in sql
    reread(sql, warehouse)


# ------------------------------------------------------------------- texte


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_le_cast_texte_nomme_le_type_de_l_entrepot(compile_on, warehouse: str):
    """BigQuery n'a pas de `varchar`, et sur Redshift un `varchar` sans
    longueur vaut `varchar(256)` : assez court pour tronquer sans prévenir."""
    sql = compile_on(
        warehouse,
        prepare_spec(
            [step("find_replace", column="libelle", find="a", replace="b")], ref="evts"
        ),
    )
    expected = {"bigquery": "string", "redshift": "varchar(max)"}.get(
        warehouse, "varchar"
    )
    assert f"cast(libelle as {expected})" in sql
    reread(sql, warehouse)


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_capitaliser_donne_toujours_une_longueur_a_substring(
    compile_on, warehouse: str
):
    """`substring(x, 2)` n'est pas accepté partout : la longueur est donnée."""
    sql = compile_on(
        warehouse,
        prepare_spec(
            [step("text_transform", column="libelle", mode="capitalize")], ref="evts"
        ),
    )
    fn = "substr" if warehouse == "bigquery" else "substring"
    assert f"{fn}(cast(libelle as " in sql and "length(cast(libelle as " in sql
    reread(sql, warehouse)


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_decouper_une_colonne_suit_l_entrepot(compile_on, warehouse: str):
    """BigQuery n'a pas `split_part` : il découpe en tableau."""
    sql = compile_on(
        warehouse,
        prepare_spec(
            [step("split_column", column="libelle", separator="-", count=2)],
            ref="evts",
        ),
    )
    if warehouse == "bigquery":
        assert "split(libelle, '-')[safe_ordinal(1)]" in sql
    else:
        assert "split_part(libelle, '-', 1)" in sql
    reread(sql, warehouse)


# ---------------------------------------------------------------- agrégats


def _group(filter: str = "", fn: str = "sum") -> dict:
    measure = {"fn": fn, "column": "montant", "alias": "mesure"}
    if filter:
        measure["filter"] = filter
    return {
        "name": "par_client",
        "type": "group",
        "inputs": [{"ref": "evts", "alias": "evts"}],
        "group_by": ["client"],
        "aggregations": [measure],
        "output": {"materialized": "table"},
    }


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_une_mesure_filtree_s_ecrit_en_case(compile_on, warehouse: str):
    """`filter (where …)` est standard, mais ni BigQuery, ni Redshift, ni
    Snowflake ne le connaissent. Le `case` équivaut et se lit partout."""
    sql = compile_on(warehouse, _group(filter="statut = 'ok'"))
    assert "filter (where" not in sql
    assert "sum(case when statut = 'ok' then montant end)" in sql
    reread(sql, warehouse)


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_la_mediane_est_celle_de_l_entrepot_et_dit_quand_elle_approche(
    compile_on, warehouse: str
):
    """Athena et BigQuery n'ont pas de médiane exacte. On écrit la leur, et le
    SQL le dit : un total approché ne doit pas se faire passer pour exact."""
    sql = compile_on(warehouse, _group(fn="median"))
    expected = {
        "athena": "approx_percentile(montant, 0.5)",
        "bigquery": "approx_quantiles(montant, 2)[offset(1)]",
        "postgres": "percentile_cont(0.5) within group (order by montant)",
    }.get(warehouse, "median(montant)")
    assert expected in sql
    assert ("médiane » est approchée" in sql) == (warehouse in ("athena", "bigquery"))
    reread(sql, warehouse)


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_le_filtre_incremental_d_un_groupe_s_ecrit_en_exists(
    compile_on, warehouse: str
):
    """`(a, b) in (select a, b …)` est refusé par Redshift et demande un
    `select as struct` sur BigQuery. Un `exists` corrélé se lit partout."""
    spec = {
        "name": "par_client",
        "type": "group",
        "inputs": [{"ref": "evts", "alias": "evts"}],
        "group_by": ["client", "statut"],
        "aggregations": [
            {"fn": "sum", "column": "montant", "alias": "total"},
            {"fn": "max", "column": "d", "alias": "d"},
        ],
        "output": {
            "materialized": "incremental",
            "incremental": {"column": "d"},
            "unique_key": ["client", "statut"],
            # Pas de stratégie écrite : chaque entrepôt reçoit la sienne.
            # `delete+insert` n'existe ni sur BigQuery ni sur Athena, et
            # l'écrire ici revenait à tester une configuration que dbt
            # refuserait de construire.
        },
    }
    sql = compile_on(warehouse, spec)
    assert "where exists (" in sql
    assert "recent.client = entree.client" in sql
    assert "recent.statut = entree.statut" in sql
    assert " in (\n" not in sql
    reread(sql, warehouse)


# ------------------------------------------------------------------- types


def test_les_types_proposes_nomment_ceux_de_l_entrepot():
    """`varchar` n'existe pas sur BigQuery, `double` ni sur PostgreSQL ni sur
    Redshift : proposer une liste figée faisait échouer le `dbt build`."""
    rcp.set_dialect("duckdb")
    assert "varchar" in rcp.types_offered() and "double" in rcp.types_offered()

    rcp.set_dialect("bigquery")
    bq = rcp.types_offered()
    assert "string" in bq and "int64" in bq and "float64" in bq
    assert "varchar" not in bq and "double" not in bq

    rcp.set_dialect("redshift")
    rs = rcp.types_offered()
    assert "varchar(max)" in rs and "double precision" in rs


def test_chaque_famille_resout_un_dialecte_sqlglot():
    """Les deux réglages doivent tomber d'accord, sinon la traduction dérape.

    `set_dialect` règle la famille depuis le nom de l'adaptateur, et le
    dialecte sqlglot seulement si sqlglot connaît ce nom. Une famille dont le
    dialecte retombe sur le générique traduirait les formats de date en
    `%d/%m/%Y` tout en écrivant le `to_timestamp` de Redshift, qui attend
    `DD/MM/YYYY` — un modèle qui compile et lit des dates fausses.
    """
    for adapter in rcp._FAMILIES:
        with rcp.using_dialect(adapter) as d:
            assert d.sqlglot, (
                f"« {adapter} » a une famille SQL mais aucun dialecte sqlglot : "
                f"ajoutez-le à _SQLGLOT_ALIAS."
            )


# --------------------------------------------------------- entrepôt inconnu


def test_un_entrepot_inconnu_refuse_plutot_que_d_ecrire_du_duckdb():
    """Écrire du DuckDB « au cas où » produisait un modèle que l'entrepôt
    refuse, et rien ne le disait avant le `dbt build`."""
    with rcp.using_dialect("clickhouse"):
        assert rcp.family() == rcp.GENERIC
        with pytest.raises(rcp.RecipeError) as exc:
            rcp.compile_recipe(
                prepare_spec(
                    [step("parse_date", column="d", format="%d/%m/%Y")], ref="evts"
                ),
                COLUMNS,
            )
    assert "ne sait pas écrire" in str(exc.value)
    assert "Formule" in str(exc.value), "l'erreur doit dire par quoi remplacer"

    # Ce qui est portable, lui, continue de passer.
    sql = rcp.compile_recipe(
        prepare_spec(
            [step("extract_date_parts", column="d", parts=["year"])], ref="evts"
        ),
        COLUMNS,
    )
    assert "extract(year from d)" in sql


# ------------------------------------------------------- littéraux de chaîne
#
# Doubler l'apostrophe est la règle du SQL standard, pas celle de tout le
# monde. BigQuery lit `'O''Reilly'` comme deux chaînes collées et refuse la
# requête ; l'antislash, symétriquement, est un caractère ordinaire sur DuckDB
# et Postgres mais une échappe sur BigQuery, Snowflake et Redshift — un `\n`
# saisi dans un filtre y devenait un retour à la ligne. Les deux cas partent
# dans un `.sql` que rien ne relisait avant le `dbt build`.

AWKWARD = [
    "O'Reilly",
    "c'est l'été",
    "chemin\\vers\\fichier",
    "ligne\\nsuite",
    "guillemet \" et apostrophe '",
]


@pytest.mark.parametrize("value", AWKWARD)
@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_un_litteral_se_relit_a_l_identique_dans_l_entrepot(warehouse: str, value: str):
    """Aller-retour : ce que l'entrepôt relira doit être ce qu'on a voulu dire."""
    with rcp.using_dialect(warehouse) as d:
        sql = f"select {rcp.lit(value)} as x"
        read_value = sqlglot.parse_one(sql, dialect=d.sqlglot).expressions[0].this
    assert read_value.this == value, f"{warehouse} : {sql}"


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_une_apostrophe_dans_un_remplacement_ne_casse_pas_le_modele(
    compile_on, warehouse: str
):
    """Le cas de bout en bout : « Rechercher & remplacer » sur un nom propre.
    Sur BigQuery, le SQL produit n'était même pas analysable."""
    sql = compile_on(
        warehouse,
        prepare_spec(
            [
                step(
                    "find_replace", column="libelle", find="O'Reilly", replace="OReilly"
                )
            ],
            ref="evts",
        ),
    )
    reread(sql, warehouse)  # lève si l'entrepôt ne sait pas relire ce qu'on a écrit


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_un_joker_cherche_a_la_lettre_suit_l_entrepot(compile_on, warehouse: str):
    """Neutraliser `%` et `_` ne s'écrit pas pareil partout.

    Tout le monde accepte une clause `escape` — sauf BigQuery, dont le `like`
    n'échappe qu'à l'antislash. Écrire `escape '!'` pour lui produirait un
    modèle que `dbt build` refuse.
    """
    sql = compile_on(
        warehouse,
        prepare_spec(
            [
                step(
                    "filter_value",
                    column="libelle",
                    operator="contains",
                    values=["50%_net"],
                    action="keep",
                )
            ],
            ref="evts",
        ),
    )
    reread(sql, warehouse)
    if warehouse == "bigquery":
        assert "escape" not in sql.lower()
        # Le littéral porte l'antislash doublé : c'est ainsi que BigQuery
        # lit un joker échappé.
        assert r"50\\%\\_net" in sql
    else:
        assert f"escape {rcp.lit(rcp.LIKE_ESCAPE)}" in sql
        assert "50!%!_net" in sql


@pytest.mark.parametrize("warehouse", WAREHOUSES)
def test_une_apostrophe_dans_un_filtre_ne_casse_pas_le_modele(
    compile_on, warehouse: str
):
    sql = compile_on(
        warehouse,
        prepare_spec(
            [
                step(
                    "filter_value",
                    column="libelle",
                    operator="contains",
                    values=["O'Reilly"],
                    action="keep",
                )
            ],
            ref="evts",
        ),
    )
    reread(sql, warehouse)


# ------------------------------------------------------- contexte de dialecte
#
# L'entrepôt courant était trois variables de module, que chaque appelant
# réglait et devait penser à rendre. Il est maintenant une valeur, et le bloc
# qui la pose dit jusqu'où elle porte.


def test_un_dialecte_se_construit_sans_rien_regler_de_global():
    """Le point de la refonte : compiler pour Snowflake sans toucher au reste.

    Tant que l'entrepôt courant était une globale, il n'existait aucune façon
    d'interroger un dialecte sans l'imposer à tout le processus — donc au test
    suivant, et à la requête voisine.
    """
    before = rcp.current_dialect()
    snow = rcp.Dialect.for_adapter("snowflake")
    assert (snow.adapter, snow.family, snow.sqlglot) == (
        "snowflake",
        "snowflake",
        "snowflake",
    )
    assert rcp.current_dialect() == before, "le construire ne doit rien régler"


def test_un_alias_de_dialecte_se_resout_sans_changer_la_famille():
    """Starburst parle le SQL de Trino, et dbt l'appelle « starburst »."""
    d = rcp.Dialect.for_adapter("starburst")
    assert (d.adapter, d.family, d.sqlglot) == ("starburst", "trino", "trino")


def test_les_trois_noms_ne_se_deduisent_pas_l_un_de_l_autre():
    """C'est pour ça qu'ils sont trois champs et non un seul réglage.

    sqlglot connaît ClickHouse — il sait donc le *parser* — alors que l'atelier
    ne sait pas écrire ses expressions non portables : famille générique,
    dialecte bien réel. Vertica, lui, n'a ni l'un ni l'autre. Confondre les
    deux cas, c'est soit refuser une expression valide, soit écrire du SQL que
    l'entrepôt rejettera.
    """
    ch = rcp.Dialect.for_adapter("clickhouse")
    assert ch.family == rcp.GENERIC and ch.sqlglot == "clickhouse"

    vertica = rcp.Dialect.for_adapter("vertica")
    assert vertica.adapter == "vertica"
    assert vertica.family == rcp.GENERIC and vertica.sqlglot == ""


def test_le_bloc_rend_l_entrepot_precedent():
    before = rcp.current_dialect()
    with rcp.using_dialect("bigquery") as d:
        assert d is rcp.current_dialect() and d.family == "bigquery"
    assert rcp.current_dialect() == before


def test_le_bloc_rend_l_entrepot_meme_quand_il_echoue():
    """Le `try/finally` que chaque appelant écrivait à la main, et oubliait."""
    before = rcp.current_dialect()
    with pytest.raises(ZeroDivisionError):
        with rcp.using_dialect("bigquery"):
            raise ZeroDivisionError
    assert rcp.current_dialect() == before


def test_les_blocs_s_imbriquent():
    with rcp.using_dialect("postgres"):
        with rcp.using_dialect("bigquery"):
            assert rcp.family() == "bigquery"
        assert rcp.family() == "postgres"


def test_un_dialecte_est_gele():
    """Une valeur, pas un réglage : on ne la modifie pas sous les pieds du SQL."""
    # `dataclass(frozen=True)` lève `FrozenInstanceError`, qui hérite de
    # `AttributeError` : on nomme la famille plutôt qu'« une exception ».
    with pytest.raises(AttributeError):
        rcp.Dialect.for_adapter("duckdb").adapter = "snowflake"

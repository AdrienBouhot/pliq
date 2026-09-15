"""Ce qui ne s'écrit pas pareil d'un entrepôt à l'autre.

« Parser une date » est `try_strptime` sur DuckDB, `date_parse` sur Athena,
`to_timestamp` sur Redshift, `parse_timestamp` sur BigQuery. Chaque geste qui
n'est pas portable s'écrit ici, famille par famille — et ce qui n'a pas
d'équivalent est refusé plutôt qu'approximé en silence.
"""

from __future__ import annotations

import re

from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

from .dialect import GENERIC, _unsupported_in_family, current_dialect
from .errors import RecipeError
from .sql import lit
from .vocabulary import LOOKBACK_UNITS

# --- texte ------------------------------------------------------------------

# `cast(x as varchar)` n'existe pas partout : BigQuery n'a que STRING, et un
# `varchar` sans longueur vaut `varchar(256)` sur Redshift — assez court pour
# tronquer une valeur sans prévenir.
_TEXT_TYPE = {"bigquery": "string", "redshift": "varchar(max)"}


def text_type() -> str:
    """Le type texte de l'entrepôt, celui vers lequel on caste pour comparer."""
    return _TEXT_TYPE.get(current_dialect().family, "varchar")


def as_text(expr: str) -> str:
    return f"cast({expr} as {text_type()})"


# Familles scalaires reconnues, par le *nom* du type et non par un morceau de
# ce nom. Chercher « INT » n'importe où dans le type faisait passer `INTERVAL`
# pour un nombre, `STRUCT(a VARCHAR)` pour du texte et `INTEGER[]` pour un
# nombre — avec trois conséquences visibles : une colonne saine affichée
# « 100 % invalide » par la barre de qualité, un `trim()` posé sur un STRUCT
# que l'entrepôt refuse, et un profil complet qui n'écartait aucun des types
# qu'il annonce écarter.
_SQL_TYPE_FAMILIES = {
    "texte": (
        "CHAR",
        "VARCHAR",
        "NVARCHAR",
        "BPCHAR",
        "NCHAR",
        "CHARACTER",
        "CHARACTER VARYING",
        "TEXT",
        "NTEXT",
        "STRING",
        "CLOB",
        "LONGTEXT",
        "MEDIUMTEXT",
        "TINYTEXT",
        "VARCHAR2",
        "NVARCHAR2",
    ),
    "booléen": ("BOOL", "BOOLEAN", "BIT"),
    "date": (
        "DATE",
        "DATETIME",
        "DATETIME2",
        "SMALLDATETIME",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "TIMESTAMP_NTZ",
        "TIMESTAMP_LTZ",
        "TIMESTAMP_TZ",
        "TIMESTAMP WITH TIME ZONE",
        "TIMESTAMP WITHOUT TIME ZONE",
        "DATETIMEOFFSET",
    ),
    "nombre": (
        "INT",
        "INT2",
        "INT4",
        "INT8",
        "INT16",
        "INT32",
        "INT64",
        "INTEGER",
        "SMALLINT",
        "TINYINT",
        "BIGINT",
        "HUGEINT",
        "UINT8",
        "UINT16",
        "UINT32",
        "UINT64",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "DECIMAL",
        "NUMERIC",
        "NUMBER",
        "DEC",
        "DOUBLE",
        "DOUBLE PRECISION",
        "FLOAT",
        "FLOAT4",
        "FLOAT8",
        "FLOAT64",
        "REAL",
        "MONEY",
        "SMALLMONEY",
    ),
}

# Types qui contiennent d'autres valeurs, ou qui n'ont pas de sémantique
# scalaire portable. On ne les range dans aucune famille scalaire : ni `trim`,
# ni `min`/`max`, ni « ce texte ressemble-t-il à un entier » n'ont de sens sur
# eux, et c'est précisément ce que l'appelant doit savoir.
_COMPOSITES = (
    "ARRAY",
    "STRUCT",
    "MAP",
    "ROW",
    "LIST",
    "RECORD",
    "JSON",
    "JSONB",
    "VARIANT",
    "OBJECT",
    "GEOGRAPHY",
    "GEOMETRY",
    "SUPER",
    "HSTORE",
    "XML",
)

COMPOSITE = "composite"


def _type_label(sql_type: str) -> str:
    """Le nom nu d'un type : paramètres, tailles et suffixe de tableau retirés.

    `DECIMAL(18, 2)` → `DECIMAL`, `VARCHAR(50)[]` → `VARCHAR[]` (composite),
    `TIMESTAMP WITH TIME ZONE` → inchangé.
    """
    t = " ".join(sql_type.upper().split())
    table_rows = t.rstrip().endswith("[]")
    for opener in ("(", "<"):
        if opener in t:
            t = t[: t.index(opener)]
    return t.strip() + ("[]" if table_rows else "")


def type_family(sql_type: str | None) -> str:
    """Le genre de valeur d'un type, pour refuser d'empiler l'ineptie.

    Rend `""` pour un type inconnu — on ne juge pas —, `"composite"` pour un
    type qui en contient d'autres, et l'une des quatre familles scalaires
    sinon. La reconnaissance est ancrée sur le nom du type : une
    sous-chaîne ne suffit pas, sans quoi `INTERVAL` serait un nombre.
    """
    raw_text = (sql_type or "").strip()
    if not raw_text:
        return ""  # type inconnu : on ne juge pas
    name = _type_label(raw_text)
    if not name:
        return ""
    # Un tableau reste un tableau quel que soit ce qu'il contient, et un type
    # paramétré est déjà réduit à son nom par `_type_label`.
    if name.endswith("[]") or name in _COMPOSITES:
        return COMPOSITE
    for family, names in _SQL_TYPE_FAMILIES.items():
        if name in names:
            return family
    return name.lower()


def _carries_text(sql_type: str | None) -> bool:
    """Une colonne où la chaîne vide existe — donc où elle veut dire « vide ».

    Le type inconnu en fait partie : l'aperçu ne connaît pas toujours le type
    d'une colonne calculée, et mieux vaut y tester une chaîne vide pour rien
    que laisser passer des cellules que le profilage compte comme vides.
    """
    return type_family(sql_type) in ("texte", "")


# --- « vide » ---------------------------------------------------------------
#
# Une seule définition, parce que l'écran n'en montre qu'une. Le profilage
# compte une cellule vide si elle est `null` ou si c'est une chaîne que rien ne
# remplit, espaces compris (`profiling._is_empty`) — et c'est ce compte qui fait
# apparaître « Remplir les cellules vides » à côté de la colonne. Les deux
# étapes proposées doivent donc parler de la même chose : « Remplir » ne
# remplaçait que les `null` et laissait le compteur de vides inchangé, tandis
# que « Supprimer » attrapait `''` mais pas une chaîne d'espaces.
#
# `trim` ne retire que les espaces là où Python retire toute l'espace blanche :
# une cellule ne contenant qu'une tabulation reste comptée vide sans être
# traitée. Aucun entrepôt visé ne partage la définition de Python, et la
# réécrire à la main coûterait un `replace` par caractère invisible.


def _needs_trim(ref: str, sql_type: str | None) -> str:
    """La colonne prête pour `trim` : castée seulement si son type est inconnu.

    `cast(nom as varchar)` sur une colonne qui est déjà du texte n'apprend rien
    au lecteur du `.sql`. Sur un type qu'on ne connaît pas, il évite que
    l'entrepôt refuse `trim` d'un JSON ou d'un tableau.
    """
    return ref if type_family(sql_type) == "texte" else as_text(ref)


def empty_sql(ref: str, sql_type: str | None) -> str:
    """« Cette cellule est vide », écrit pour l'entrepôt."""
    if not _carries_text(sql_type):
        return f"{ref} is null"
    return f"({ref} is null or trim({_needs_trim(ref, sql_type)}) = '')"


def filled_sql(ref: str, sql_type: str | None) -> str:
    """« Cette cellule porte une valeur » — la négation exacte d'`empty_sql`."""
    if not _carries_text(sql_type):
        return f"{ref} is not null"
    return f"{ref} is not null and trim({_needs_trim(ref, sql_type)}) <> ''"


# `%` et `_` sont les jokers de `like` : interpolés tels quels, « contient 50% »
# ramenait tout ce qui contient « 50 », et « contient _ » toute ligne d'au moins
# un caractère. L'écran propose une recherche de valeur et n'annonce aucune
# syntaxe de motifs : on neutralise donc les jokers de ce que l'utilisateur a
# tapé. Avec l'action « retirer », c'étaient des lignes sans le caractère
# cherché qui disparaissaient du résultat.
#
# Le caractère d'échappement est `!` plutôt que l'antislash : celui-ci est déjà
# une échappe *dans le littéral* chez Snowflake, Redshift et BigQuery, et il
# aurait fallu compter les doublements à deux niveaux. `!` ne veut rien dire
# nulle part, ni dans un littéral ni dans un motif.
LIKE_ESCAPE = "!"


def like_literal(value: str) -> tuple[str, str]:
    """(motif cherchant `value` à la lettre, clause `escape` qui va avec).

    BigQuery est le seul de la bande à ne pas accepter de clause `escape` : son
    `like` échappe à l'antislash, et rien d'autre. Le littéral s'écrit alors
    avec l'antislash doublé — ce dont `lit()` se charge, puisque c'est sqlglot
    qui pose les quotes.
    """
    text = str(value)
    if current_dialect().family == "bigquery":
        return re.sub(r"([\\%_])", r"\\\1", text), ""
    pattern = re.sub(f"([{re.escape(LIKE_ESCAPE)}%_])", rf"{LIKE_ESCAPE}\1", text)
    return pattern, f" escape {lit(LIKE_ESCAPE)}"


def like_predicate(expr: str, value: str, *, start: str, end: str) -> str:
    """`expr like '<debut>valeur<fin>'`, la valeur prise à la lettre."""
    pattern, escaped = like_literal(value)
    return f"{expr} like {lit(start + pattern + end)}{escaped}"


def substring(expr: str, start: int | str, length: int | str) -> str:
    """`substring` partout, sauf BigQuery qui ne connaît que `substr`.

    La longueur est toujours donnée : la forme à deux arguments n'est pas
    acceptée par tous les entrepôts.
    """
    fn = "substr" if current_dialect().family == "bigquery" else "substring"
    return f"{fn}({expr}, {start}, {length})"


def split_part(expr: str, sep: str, index: int) -> str:
    if current_dialect().family == "bigquery":
        # BigQuery n'a pas `split_part` : il découpe en tableau. `safe_ordinal`
        # rend null quand le morceau n'existe pas, là où `ordinal` échouerait.
        return f"split({expr}, {sep})[safe_ordinal({index})]"
    return f"split_part({expr}, {sep}, {index})"


# --- dates ------------------------------------------------------------------

# Conversion texte → horodatage, avec un format. La forme « sûre » (null au
# lieu d'une erreur sur une valeur qui ne parse pas) n'existe pas partout :
# Redshift et PostgreSQL échouent sur la ligne fautive, ce que l'aide de
# l'étape annonce.
_PARSE_TS = {
    "duckdb": "try_strptime({expr}, {fmt})",
    "trino": "try(date_parse({expr}, {fmt}))",
    "bigquery": "safe.parse_timestamp({fmt}, {expr})",
    "snowflake": "try_to_timestamp({expr}, {fmt})",
    "redshift": "to_timestamp({expr}, {fmt})",
    "postgres": "to_timestamp({expr}, {fmt})",
}

# Conversion sans format : le cast qui rend null plutôt que d'échouer.
_SAFE_CAST = {
    "duckdb": "try_cast({expr} as {type})",
    "trino": "try_cast({expr} as {type})",
    "snowflake": "try_cast({expr} as {type})",
    "bigquery": "safe_cast({expr} as {type})",
    "redshift": "cast({expr} as {type})",
    "postgres": "cast({expr} as {type})",
    GENERIC: "cast({expr} as {type})",
}


def time_format(fmt: str) -> str:
    """Le format strptime saisi, traduit dans celui de l'entrepôt, et quoté.

    DuckDB, Athena et BigQuery lisent `%d/%m/%Y` ; Redshift et PostgreSQL
    veulent `DD/MM/YYYY`. C'est sqlglot qui connaît la correspondance : la
    réécrire à la main se serait trompée sur les cas rares (`%j`, `%U`…).
    """
    gen = Dialect.get_or_raise(current_dialect().sqlglot).generator()
    return gen.format_time(
        exp.StrToTime(this=exp.column("x"), format=exp.Literal.string(fmt))
    )


def parse_timestamp(expr: str, fmt: str) -> str:
    tpl = _PARSE_TS.get(current_dialect().family)
    if tpl is None:
        raise _unsupported_in_family(
            "une conversion de texte en date à format",
            "utilisez « Formule » pour écrire l'expression de votre entrepôt.",
        )
    return tpl.format(expr=expr, fmt=time_format(fmt))


def safe_cast(expr: str, sql_type: str) -> str:
    return _SAFE_CAST.get(current_dialect().family, _SAFE_CAST[GENERIC]).format(
        expr=expr, type=sql_type
    )


# `extract(<partie> from x)` est du SQL standard et passe partout, à ceci près
# que le jour de la semaine ne s'y nomme pas pareil — et ne se numérote pas
# pareil non plus, ce que l'aide de l'étape dit.
_DOW = {
    "trino": "day_of_week",
    "bigquery": "dayofweek",
    "snowflake": "dayofweek",
}

DATE_PARTS = ("year", "month", "day", "quarter", "week", "dow")


def date_part(part: str, expr: str) -> str:
    if part not in DATE_PARTS:
        raise RecipeError(f"Composant de date inconnu : {part}")
    name = _DOW.get(current_dialect().family, "dow") if part == "dow" else part
    return f"extract({name} from {expr})"


# --- agrégats ---------------------------------------------------------------

# La médiane exacte n'existe pas partout. Là où elle manque, on écrit la
# fonction approchée de l'entrepôt plutôt que rien — et le SQL le dit en
# toutes lettres, parce qu'un total « médiane » approché ne doit pas se faire
# passer pour exact.
_MEDIAN = {
    "duckdb": "median({0})",
    "redshift": "median({0})",
    "snowflake": "median({0})",
    "postgres": "percentile_cont(0.5) within group (order by {0})",
    GENERIC: "percentile_cont(0.5) within group (order by {0})",
    "trino": "approx_percentile({0}, 0.5)",
    "bigquery": "approx_quantiles({0}, 2)[offset(1)]",
}

APPROX_MEDIAN = ("trino", "bigquery")


def median(expr: str) -> str:
    return _MEDIAN.get(current_dialect().family, _MEDIAN[GENERIC]).format(expr)


def median_is_approximate() -> bool:
    return current_dialect().family in APPROX_MEDIAN


# --- recul d'une borne de date -----------------------------------------------

# Reculer un horodatage de N unités ne s'écrit pas pareil partout, et BigQuery
# demande en plus une fonction différente selon que la colonne est une date,
# une date-heure ou un horodatage. Les autres familles ne regardent pas le
# type : une seule écriture leur suffit.
_DATE_SUB = {
    "duckdb": "{expr} - interval '{n} {unit}'",
    "postgres": "{expr} - interval '{n} {unit}'",
    "redshift": "dateadd({unit}, -{n}, {expr})",
    "snowflake": "dateadd({unit}, -{n}, {expr})",
    "trino": "date_add('{unit}', -{n}, {expr})",
}

_BQ_DATE_SUB = {
    "date": "date_sub",
    "datetime": "datetime_sub",
    "timestamp": "timestamp_sub",
}


def _bigquery_kind(sql_type: str | None) -> str | None:
    t = (sql_type or "").upper()
    if "TIMESTAMP" in t:
        return "timestamp"
    if "DATETIME" in t:
        return "datetime"
    if "DATE" in t:
        return "date"
    return None


def date_minus(expr: str, n: int, unit: str, sql_type: str | None = None) -> str:
    """`expr` reculé de `n` `unit`, dans le SQL de l'entrepôt.

    Sur BigQuery le nom de la fonction dépend du type de la colonne, et se
    tromper ne donne pas une valeur fausse mais une requête refusée. Plutôt que
    de parier sur `timestamp_sub`, on refuse quand le type est inconnu — avec
    de quoi le rendre connu.
    """
    if unit not in LOOKBACK_UNITS:
        raise RecipeError(f"Unité de reprise inconnue : {unit}")
    n = int(n)  # une seule fois : toutes les familles l'interpolent telle quelle

    if current_dialect().family == "bigquery":
        kind = _bigquery_kind(sql_type)
        if kind is None:
            raise _unsupported_in_family(
                "une fenêtre de reprise sans connaître le type de la colonne",
                "BigQuery veut date_sub, datetime_sub ou timestamp_sub selon "
                "que la colonne est une date, une date-heure ou un horodatage. "
                "Ajoutez une étape « Changer le type » avant, ou retirez la "
                "fenêtre de reprise.",
            )
        if kind == "date" and unit == "hour":
            raise RecipeError(
                "Une fenêtre de reprise en heures n'a pas de sens sur une "
                "colonne de type date : comptez en jours."
            )
        return f"{_BQ_DATE_SUB[kind]}({expr}, interval {n} {unit})"

    tpl = _DATE_SUB.get(current_dialect().family)
    if tpl is None:
        raise _unsupported_in_family(
            "une fenêtre de reprise incrémentale",
            "retirez-la, ou écrivez la borne à la main dans une recipe SQL.",
        )
    return tpl.format(expr=expr, n=n, unit=unit)


# --- types proposés ---------------------------------------------------------

# « Changer le type » propose une liste : elle doit nommer des types que
# l'entrepôt connaît. `varchar` n'existe pas sur BigQuery, `double` pas sur
# PostgreSQL ni Redshift.
_TYPE_ALIASES = {
    "bigquery": {
        "varchar": "string",
        "integer": "int64",
        "bigint": "int64",
        "double": "float64",
        "decimal(18,2)": "numeric(18,2)",
        "boolean": "bool",
    },
    "postgres": {"double": "double precision"},
    "redshift": {"varchar": "varchar(max)", "double": "double precision"},
}

_TYPES = (
    "varchar",
    "integer",
    "bigint",
    "double",
    "decimal(18,2)",
    "date",
    "timestamp",
    "boolean",
)


def types_offered() -> list[str]:
    """Les types proposés par « Changer le type », dits comme l'entrepôt les dit."""
    alias = _TYPE_ALIASES.get(current_dialect().family, {})
    seen: list[str] = []
    for t in _TYPES:
        concrete = alias.get(t, t)
        if concrete not in seen:
            seen.append(concrete)
    return seen

"""Citer un identifiant, écrire un littéral — avec la syntaxe de l'entrepôt.

BigQuery veut des backticks là où le standard veut des guillemets, et refuse
`'O''Reilly'` là où tout le monde l'accepte. C'est sqlglot qui pose les
quotes, à partir du dialecte courant.
"""

from __future__ import annotations

import re
from typing import Any

from sqlglot import exp

from .dialect import current_dialect
from .errors import RecipeError

SIMPLE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# Ce qu'un nom dbt ne contient jamais, et qui casserait le Jinja qu'on écrit
# autour de lui. Le reste — tirets, espaces, accents — appartient à dbt.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_NAME = 200
# Tout ce qu'un nom de CTE ne peut pas porter.
_FORBIDDEN_ALIAS_RE = re.compile(r"[^a-zA-Z0-9_]")


# --------------------------------------------------------------------- SQL util


# Les mots réservés SQL ont la forme d'un identifiant simple mais n'en sont
# pas un : `select order from t` ne parse pas. La liste est l'union des mots
# que PostgreSQL/DuckDB, BigQuery et Snowflake réservent — l'union et non le
# jeu du seul entrepôt courant, pour qu'une recipe écrite sur DuckDB reste
# valide le jour où le même projet part sur Snowflake.
#
# Citer un mot qui n'était pas réservé ne change rien : le nom vient de
# l'entrepôt, on le réécrit donc exactement tel qu'il y est stocké.
RESERVED = frozenset("""
    account all alter analyse analyze and any array as asc assert_rows_modified
    asymmetric at between both by case cast check collate column connect
    connection constraint contains create cross cube current current_catalog
    current_date current_role current_time current_timestamp current_user
    database default deferrable define delete desc distinct do drop else end
    enum escape except exclude exists extract false fetch following for foreign
    from full grant group grouping groups gscluster hash having if ignore ilike
    in increment initially inner insert intersect interval into is issue join
    lateral leading left like limit localtime localtimestamp lookup merge minus
    natural new no not null nulls of offset on only or order organization outer
    over partition placing preceding primary proto qualify range recursive
    references regexp respect returning revoke right rlike rollup row rows
    sample schema select session_user set some start struct symmetric table
    tablesample then to trailing treat trigger true try_cast unbounded union
    unique unnest update user using values variadic view when whenever where
    window with within
    """.split())


def q(ident: str) -> str:
    """Cite un identifiant si nécessaire, avec le guillemet de l'entrepôt.

    C'est sqlglot qui pose les guillemets : BigQuery veut des backticks, et
    `"order"` y désignerait une chaîne de caractères plutôt qu'une colonne.
    """
    ident = (ident or "").strip()
    if not ident:
        raise RecipeError("Nom de colonne vide.")
    if SIMPLE_IDENT.match(ident) and ident not in RESERVED:
        return ident
    return exp.to_identifier(ident, quoted=True).sql(dialect=current_dialect().sqlglot)


def lit(value: Any) -> str:
    """Littéral SQL, écrit avec l'échappement de l'entrepôt.

    Doubler l'apostrophe est la règle du SQL standard, pas celle de tout le
    monde : BigQuery refuse `'O''Reilly'` — il y lit deux chaînes collées — et
    l'attend écrit `'O\\'Reilly'`. L'antislash, symétriquement, est un caractère
    ordinaire sur DuckDB et Postgres mais une échappe sur BigQuery, Snowflake
    et Redshift : un `\\n` saisi dans un filtre y devenait un retour à la ligne.

    C'est donc sqlglot qui pose les quotes, comme il pose déjà les guillemets
    des identifiants dans `q()`.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return exp.Literal.string(str(value)).sql(dialect=current_dialect().sqlglot)


# `float()` est trop accueillant pour servir de garde : il accepte « inf »,
# « nan » et « 1_000 ». Écrits tels quels dans le SQL, l'entrepôt les refuse —
# ou les lit de travers. On ne laisse passer nu que ce qui est un vrai
# littéral numérique, et le texte part inchangé plutôt que via un float, qui
# abîmerait un NUMERIC(38,0).
_NUMERIC_LIT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def typed_lit(value: Any, sql_type: str | None) -> str:
    """Littéral non quoté pour les colonnes numériques/booléennes."""
    t = (sql_type or "").upper()
    text = str(value).strip()
    if any(
        k in t
        for k in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT")
    ):
        return text if _NUMERIC_LIT.match(text) else lit(value)
    if "BOOL" in t and text.lower() in ("true", "false"):
        return text.lower()
    return lit(value)


def as_int(v: Any) -> int:
    """Un entier, ou zéro : ce que l'entrepôt rapporte n'est pas toujours un."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0

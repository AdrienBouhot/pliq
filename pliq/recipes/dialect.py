"""L'entrepôt pour lequel on écrit, et le réglage qui le désigne.

Un même geste ne s'écrit pas pareil partout. Ce module ne sait rien du SQL :
il dit seulement de quel entrepôt il s'agit, et sous quels trois noms — celui
de dbt, celui de la famille, celui de sqlglot. Ce qui s'écrit différemment
selon la famille est dans `writers`.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import sqlglot

from .errors import RecipeError

# ------------------------------------------------------------------ dialecte
#
# Un même geste ne s'écrit pas pareil partout : « parser une date » est
# `try_strptime` sur DuckDB, `date_parse` sur Athena, `to_timestamp` sur
# Redshift, `parse_timestamp` sur BigQuery. Écrire du DuckDB pour tout le monde
# produisait un modèle que l'entrepôt refuse — et rien ne le disait avant le
# `dbt build`. On regroupe donc les adaptateurs dbt par famille de SQL, et
# chaque expression qui n'est pas portable s'écrit famille par famille.
#
# Ce qui n'a pas d'équivalent dans une famille n'est pas approximé en silence :
# l'étape est refusée, avec de quoi la remplacer.

GENERIC = "generic"  # entrepôt dont l'atelier ne connaît pas le SQL

_FAMILIES = {
    "duckdb": "duckdb",
    "postgres": "postgres",
    "redshift": "redshift",
    "athena": "trino",
    "trino": "trino",
    "presto": "trino",
    "starburst": "trino",
    "bigquery": "bigquery",
    "snowflake": "snowflake",
}

# Les entrepôts que l'atelier sait écrire, pour le dire quand il ne sait pas.
_KNOWN_WAREHOUSES = "DuckDB, PostgreSQL, Redshift, Athena, BigQuery et Snowflake"

# sqlglot ne sert qu'à refuser une expression manifestement invalide avant
# qu'elle parte dans un fichier. On le règle sur l'entrepôt du projet : une
# expression Snowflake ne doit pas être rejetée parce qu'elle déplaît à DuckDB.
_SQLGLOT_ALIAS = {"starburst": "trino"}


def _sqlglot_name(adapter: str) -> str:
    """Le dialecte sqlglot d'un adaptateur dbt, ou « » s'il n'en a pas.

    Les noms de dialecte sqlglot recouvrent la plupart de ceux des adaptateurs
    dbt. Quelques-uns n'en ont pas à leur nom mais parlent celui d'un autre :
    sans cet alias, ils retombaient sur le SQL générique pour la validation
    *et* pour la traduction des formats de date.
    """
    name = _SQLGLOT_ALIAS.get(adapter, adapter)
    try:
        sqlglot.parse_one("select 1", dialect=name)
    except Exception:  # noqa: BLE001 — dialecte inconnu de sqlglot
        return ""
    return name


@dataclass(frozen=True)
class Dialect:
    """L'entrepôt pour lequel on écrit, et les trois noms qu'il porte.

    Ils ne coïncident pas, et les confondre écrit du SQL faux : `adapter` est
    le nom que dbt donne à sa connexion, celui que les messages citent ;
    `family` regroupe les adaptateurs qui écrivent le même SQL — Athena, Trino,
    Presto et Starburst n'en forment qu'une ; `sqlglot` est le dialecte du
    parseur, vide quand sqlglot ne connaît pas l'entrepôt.

    Gelé, et constructible seul : `Dialecte.pour("snowflake")` s'écrit dans un
    test ou dans un autre programme sans rien régler de global, ce qui était
    justement impossible tant que ces trois noms étaient trois variables de
    module.
    """

    adapter: str = ""
    family: str = GENERIC
    sqlglot: str = ""

    @classmethod
    def for_adapter(cls, adapter_type: str | None) -> "Dialect":
        """Le dialecte d'un adaptateur dbt, nommé comme dbt le nomme."""
        name = (adapter_type or "").strip().lower()
        return cls(name, _FAMILIES.get(name, GENERIC), _sqlglot_name(name))


GENERIC_DIALECT = Dialect()  # entrepôt que l'atelier ne connaît pas

# Un seul entrepôt à la fois : l'atelier a un projet ouvert, et le compilateur
# écrit pour celui-là. Ce n'est donc pas un `ContextVar` — une bascule de
# projet vaut pour les requêtes suivantes, et un `ContextVar` réglé au fond
# d'une requête serait rendu avec elle, c'est-à-dire perdu. Ce qui borne
# l'accès, c'est le `project_lock` du serveur, qui sérialise déjà les routes.
#
# Ce que ce module doit en revanche à ses appelants, c'est de savoir *jusqu'où*
# un réglage porte : `using_dialect()` le dit et le rend, `set_dialect()` ne vaut
# que pour l'ouverture d'un projet, qui n'a pas de fin.
_CURRENT = GENERIC_DIALECT


def current_dialect() -> Dialect:
    """L'entrepôt pour lequel on écrit en ce moment."""
    return _CURRENT


@contextmanager
def using_dialect(adapter_type: str | None):
    """Écrire pour cet entrepôt le temps du bloc, puis rendre le précédent.

    C'est la porte d'entrée normale : un test qui compare six entrepôts, un
    programme qui réutilise le compilateur, une compilation ponctuelle hors du
    projet ouvert. Rendre le précédent est ce qu'une suite de `set_dialect()`
    obligeait chaque appelant à faire lui-même, en `try/finally`, et qu'aucun
    ne pouvait oublier sans contaminer le test suivant.
    """
    global _CURRENT
    before = _CURRENT
    _CURRENT = Dialect.for_adapter(adapter_type)
    try:
        yield _CURRENT
    finally:
        _CURRENT = before


def set_dialect(adapter_type: str | None) -> str:
    """Règle l'entrepôt pour la suite : l'ouverture d'un projet, et elle seule.

    Sans bloc qui la borne, parce qu'une bascule de projet n'a pas de fin.
    Partout ailleurs, `using_dialect()` dit jusqu'où le réglage porte.
    """
    global _CURRENT
    _CURRENT = Dialect.for_adapter(adapter_type)
    return _CURRENT.sqlglot


def family() -> str:
    """La famille SQL de l'entrepôt courant."""
    return _CURRENT.family


def _unsupported_in_family(what: str, fallback: str) -> RecipeError:
    warehouse = (
        f"« {current_dialect().adapter} »"
        if current_dialect().adapter
        else "de ce projet"
    )
    return RecipeError(
        f"L'atelier ne sait pas écrire {what} pour l'entrepôt {warehouse} : "
        f"{fallback} Les entrepôts qu'il sait écrire : {_KNOWN_WAREHOUSES}."
    )

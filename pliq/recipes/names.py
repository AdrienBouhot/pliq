"""Ce qu'on accepte d'écrire dans un fichier dbt : noms et expressions.

sqlglot ne sert ici qu'à refuser une expression manifestement invalide avant
qu'elle parte dans un `.sql` — pas à la réécrire.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .dialect import current_dialect
from .errors import RecipeError
from .sql import NAME_RE, _CTRL_RE, _MAX_NAME

# sqlglot souligne le morceau fautif avec des codes ANSI. Dans un terminal
# c'est utile ; dans une infobulle de navigateur, ça s'affiche en charabia.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _without_ansi(exc: Exception) -> str:
    return _ANSI_RE.sub("", str(exc))


def _single_statement_only(expr: str, context: str, what: str) -> None:
    """Refuse ce qui est une *suite* d'instructions plutôt qu'une expression.

    `parse_one` rend la première instruction et ne dit rien de la seconde :
    `check_expr("1; select 2")` était accepté tel quel, et
    `check_predicate("true limit 0")` faisait passer une clause du `select`
    de validation pour une condition. Ce n'est pas une barrière d'exécution —
    l'atelier offre déjà, par conception, d'écrire du SQL et du Jinja
    arbitraires dans une recipe « SQL » — mais une promesse : la fonction dit
    valider *une* expression, et doit donc en valider une.

    `parse` voit toutes les instructions, et un `;` au milieu d'un littéral ou
    d'un commentaire ne le trompe pas — c'est le parseur qui tranche, pas une
    recherche de caractère.
    """
    try:
        trees = sqlglot.parse(expr, dialect=current_dialect().sqlglot)
    except ParseError:
        return  # le parse principal dira mieux pourquoi
    if len([a for a in trees if a is not None]) > 1:
        raise RecipeError(
            f"{what} tient en une seule instruction ({context}) : "
            f"le point-virgule ne sépare rien ici."
        )


def check_expr(expr: str, context: str) -> str:
    expr = (expr or "").strip()
    while expr.endswith(";"):
        expr = expr[:-1].rstrip()
    if not expr:
        raise RecipeError(f"Expression vide ({context}).")
    try:
        tree = sqlglot.parse_one(expr, dialect=current_dialect().sqlglot)
    except ParseError as exc:
        raise RecipeError(f"SQL invalide ({context}) : {_without_ansi(exc)}") from None
    _single_statement_only(expr, context, "Une expression")
    # Une expression n'est pas une instruction : `select 2` se parse très bien
    # et n'a rien à faire dans une colonne calculée.
    if (
        isinstance(tree, exp.Query)
        or isinstance(tree, exp.DDL)
        or isinstance(tree, exp.DML)
    ):
        raise RecipeError(
            f"Expression attendue ({context}), et non une requête : "
            f"« {expr[:60]} »."
        )
    return expr


def check_predicate(expr: str, context: str) -> str:
    expr = (expr or "").strip()
    while expr.endswith(";"):
        expr = expr[:-1].rstrip()
    if not expr:
        raise RecipeError(f"Condition vide ({context}).")
    try:
        tree = sqlglot.parse_one(
            f"select 1 where {expr}", dialect=current_dialect().sqlglot
        )
    except ParseError as exc:
        raise RecipeError(
            f"Condition invalide ({context}) : {_without_ansi(exc)}"
        ) from None
    _single_statement_only(expr, context, "Une condition")
    # `true limit 0` se parsait sans un mot : la clause n'atterrissait pas dans
    # le `where` mais à côté, et une condition validée ramenait zéro ligne. On
    # exige donc que le `select` de validation n'ait reçu *que* son `where`.
    rest = [
        key
        for key, value in (tree.args if tree is not None else {}).items()
        if key not in ("expressions", "where", "from") and value
    ]
    if rest:
        raise RecipeError(
            f"Condition invalide ({context}) : « {expr[:60]} » ajoute une "
            f"clause SQL ({', '.join(sorted(rest))}) au lieu de se borner à "
            f"une condition."
        )
    return expr


def validate_name(name: str, what: str = "modèle") -> str:
    """Un nom que l'atelier *crée* : il doit être un identifiant dbt sage.

    À ne pas confondre avec `dbt_name()`, qui contrôle un nom que le projet
    porte déjà. Un modèle qu'on écrit, une couche, un alias de CTE : c'est nous
    qui choisissons, donc autant choisir bien.
    """
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise RecipeError(
            f"Nom de {what} invalide : « {name} ». Lettres, chiffres et tirets bas, "
            f"en commençant par une lettre."
        )
    return name


def dbt_name(name: str, what: str = "nom") -> str:
    """Un nom que dbt porte déjà, et que nous ne faisons que désigner.

    Une table de source s'appelle comme elle s'appelle dans l'entrepôt :
    `order-items`, `2024_sales`, `Clients Été`. dbt les accepte, et `source()`
    les lit très bien. Leur appliquer la règle d'un nom de modèle — ce que
    faisait `ref_sql` — refusait dans l'aperçu une source que l'atelier venait
    lui-même de déclarer, et que `dbt build` construisait sans broncher.

    Ce qui reste refusé est ce qu'aucun nom dbt ne contient et qui casserait le
    Jinja qu'on écrit autour : les caractères de contrôle, retour à la ligne
    compris. Les apostrophes, elles, ne sont pas refusées mais échappées —
    c'est `jinja_str` qui s'en charge, et c'est lui le garde-fou maintenant que
    ce n'est plus la règle des identifiants.
    """
    text = (name or "").strip()
    if not text:
        raise RecipeError(f"Nom de {what} vide.")
    if _CTRL_RE.search(text):
        raise RecipeError(
            f"Nom de {what} invalide : « {text} ». Un nom dbt tient sur une "
            f"ligne, sans caractère de contrôle."
        )
    if len(text) > _MAX_NAME:
        raise RecipeError(
            f"Nom de {what} trop long ({len(text)} caractères, {_MAX_NAME} au plus)."
        )
    return text


def jinja_str(value: Any) -> str:
    """Un littéral de chaîne Jinja, apostrophes et antislashes échappés.

    Jinja lit ses chaînes comme Python : l'antislash y échappe, et il doit donc
    être échappé le premier. Sans ça, un nom qui porte une apostrophe fermait la
    chaîne et faisait passer la suite pour de l'expression.
    """
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"

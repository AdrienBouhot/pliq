"""Les étapes d'une recipe Préparer : une fonction par geste.

Chacune reçoit l'état des colonnes et les paramètres saisis, met l'état à jour
et rend le SQL de son CTE. `@processor` les déclare à la bibliothèque que
l'interface affiche.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable

from .columns import ColumnState, StepSql
from .errors import RecipeError
from .names import check_expr, check_predicate, validate_name
from .sql import NAME_RE, lit, q, typed_lit
from .vocabulary import PIVOT_AGGREGATIONS
from .writers import (
    as_text,
    date_part,
    empty_sql,
    filled_sql,
    like_predicate,
    parse_timestamp,
    safe_cast,
    split_part,
    substring,
    type_family,
    _carries_text,
)

# ------------------------------------------------------------------ processeurs

PROCESSORS: dict[str, dict] = {}


def processor(
    key: str,
    label: str,
    category: str,
    summary: str,
    help_text: str = "",
    shortcut: bool = False,
):
    def wrap(fn: Callable):
        PROCESSORS[key] = {
            "key": key,
            "label": label,
            "category": category,
            "summary": summary,
            "help": help_text,
            "shortcut": shortcut,
            "fn": fn,
        }
        return fn

    return wrap


def _passthrough(state: ColumnState) -> list[tuple[str, str]]:
    return [(q(c), c) for c in state.columns]


# --- Colonnes ---------------------------------------------------------------


@processor(
    "rename",
    "Renommer des colonnes",
    "Colonnes",
    "Renomme une ou plusieurs colonnes.",
    shortcut=False,
)
def p_rename(state: ColumnState, params: dict) -> StepSql:
    renames = params.get("renames") or []
    if not renames:
        raise RecipeError("Renommer : aucune colonne indiquée.")
    mapping: dict[str, str] = {}
    for r in renames:
        src = r.get("from")
        dst = validate_name(r.get("to", ""), "colonne")
        state.require(src, "Renommer")
        mapping[src] = dst

    select = []
    for c in state.columns:
        target = mapping.get(c, c)
        select.append((q(c), target))
    new_cols = [mapping.get(c, c) for c in state.columns]
    new_types = {mapping.get(c, c): t for c, t in state.types.items()}
    state.columns, state.types = new_cols, new_types
    return StepSql(select=select, cte="renomme")


@processor(
    "keep_delete",
    "Supprimer / conserver des colonnes",
    "Colonnes",
    "Ne garde que certaines colonnes, ou en retire.",
)
def p_keep_delete(state: ColumnState, params: dict) -> StepSql:
    cols = params.get("columns") or []
    action = params.get("action", "delete")
    for c in cols:
        state.require(c, "Supprimer / conserver")
    if action == "keep":
        kept = [c for c in state.columns if c in cols]
    else:
        kept = [c for c in state.columns if c not in cols]
    if not kept:
        raise RecipeError("Supprimer / conserver : il ne resterait aucune colonne.")
    state.columns = kept
    state.types = {c: t for c, t in state.types.items() if c in kept}
    return StepSql(select=[(q(c), c) for c in kept], cte="colonnes")


@processor(
    "concat_columns",
    "Concaténer des colonnes",
    "Colonnes",
    "Assemble plusieurs colonnes en une seule.",
)
def p_concat(state: ColumnState, params: dict) -> StepSql:
    cols = params.get("columns") or []
    into = validate_name(params.get("into", ""), "colonne")
    sep = params.get("separator", " ")
    if len(cols) < 2:
        raise RecipeError("Concaténer : choisissez au moins deux colonnes.")
    for c in cols:
        state.require(c, "Concaténer")
    parts = f" || {lit(sep)} || ".join(f"coalesce({as_text(q(c))}, '')" for c in cols)
    select = [*_passthrough(state), (parts, into)]
    state.columns.append(into)
    state.types[into] = "VARCHAR"
    return StepSql(select=select, cte="concatene")


@processor(
    "split_column",
    "Découper une colonne",
    "Colonnes",
    "Découpe une colonne texte sur un séparateur.",
)
def p_split(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Découper")
    sep = params.get("separator", ",")
    count = _positive(params.get("count"), 2, "Découper", "Le nombre de morceaux")
    if not 1 <= count <= 10:
        raise RecipeError("Découper : entre 1 et 10 morceaux.")
    select = _passthrough(state)
    for i in range(1, count + 1):
        alias = f"{col}_{i}"
        select.append((split_part(q(col), lit(sep), i), alias))
        state.columns.append(alias)
        state.types[alias] = "VARCHAR"
    return StepSql(select=select, cte="decoupe")


# --- Formules ---------------------------------------------------------------


@processor(
    "formula",
    "Formule",
    "Formules",
    "Crée une colonne à partir d'une expression SQL.",
    shortcut=True,
    help_text="L'expression est du SQL de votre entrepôt, écrit tel quel : "
    "amount_eur * 1.2, upper(country), datediff('day', a, b)… L'atelier ne la "
    "traduit pas d'un entrepôt à l'autre.",
)
def p_formula(state: ColumnState, params: dict) -> StepSql:
    into = validate_name(params.get("into", ""), "colonne")
    expr = check_expr(params.get("expression", ""), "Formule")
    select = _passthrough(state)
    if into in state.columns:
        select = [(expr, c) if c == into else (e, c) for e, c in select]
    else:
        select.append((expr, into))
        state.columns.append(into)
    state.types[into] = None
    return StepSql(select=select, cte="formule")


@processor(
    "if_then_else",
    "Si, alors, sinon",
    "Formules",
    "Crée une colonne conditionnelle.",
    shortcut=True,
)
def p_if(state: ColumnState, params: dict) -> StepSql:
    into = validate_name(params.get("into", ""), "colonne")
    cond = check_predicate(params.get("condition", ""), "Si, alors, sinon")
    then = check_expr(params.get("then", "true"), "valeur « alors »")
    other = check_expr(params.get("otherwise", "null"), "valeur « sinon »")
    expr = f"case when {cond} then {then} else {other} end"
    select = _passthrough(state)
    if into in state.columns:
        select = [(expr, c) if c == into else (e, c) for e, c in select]
    else:
        select.append((expr, into))
        state.columns.append(into)
    state.types[into] = None
    return StepSql(select=select, cte="condition")


# --- Filtrage ---------------------------------------------------------------

OPERATORS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def _predicate(state: ColumnState, params: dict) -> str:
    col = state.require(params.get("column"), "Filtrer")
    op = params.get("operator", "eq")
    values = params.get("values") or (
        [params["value"]] if params.get("value") is not None else []
    )
    t = state.type_of(col)
    ref = q(col)

    def single_value(label: str):
        """La valeur d'un opérateur qui n'en compare qu'une — ou un refus.

        Onze opérateurs sur douze ne lisaient que `values[0]` et jetaient le
        reste en silence, pendant que la carte de l'étape affichait toute la
        liste : l'écran annonçait un filtre qui n'était pas celui qui
        s'exécutait. « vaut » au pluriel, c'est « est dans », et il existe
        déjà — mieux vaut y renvoyer que deviner.
        """
        if not values:
            raise RecipeError("Filtrer : aucune valeur indiquée.")
        if len(values) > 1:
            raise RecipeError(
                f"Filtrer : « {label} » compare une seule valeur, et "
                f"{len(values)} ont été indiquées. Pour en accepter plusieurs, "
                f"utilisez « est dans »."
            )
        return values[0]

    if op == "empty":
        # La définition de « vide » est unique dans l'atelier, et c'est
        # `writers` qui la tient : une cellule d'espaces est vide pour
        # « Supprimer les lignes vides », pour « Remplir les cellules vides »
        # et pour le compteur de la grille. Réécrire ici une variante qui ne
        # voyait que `null` et `''` faisait dire deux choses au même mot, sur
        # le même écran.
        return empty_sql(ref, t)
    if op == "not_empty":
        return filled_sql(ref, t)
    if op == "in":
        if not values:
            raise RecipeError("Filtrer : aucune valeur indiquée.")
        return f"{ref} in ({', '.join(typed_lit(v, t) for v in values)})"
    if op == "not_in":
        if not values:
            raise RecipeError("Filtrer : aucune valeur indiquée.")
        return f"{ref} not in ({', '.join(typed_lit(v, t) for v in values)})"
    if op in ("contains", "starts_with", "ends_with"):
        label = {
            "contains": "contient",
            "starts_with": "commence par",
            "ends_with": "finit par",
        }[op]
        start = "" if op == "starts_with" else "%"
        end = "" if op == "ends_with" else "%"
        return like_predicate(as_text(ref), single_value(label), start=start, end=end)
    if op in OPERATORS:
        return f"{ref} {OPERATORS[op]} {typed_lit(single_value(OPERATORS[op]), t)}"
    raise RecipeError(f"Opérateur de filtre inconnu : {op}")


def _keep_or_remove(predicate: str, action: str) -> str:
    """Le `where` d'un filtre, selon qu'on garde ou qu'on retire.

    `not (x = 'a')` vaut NULL quand x est vide, et une ligne dont le `where`
    vaut NULL est écartée : « retirer les lignes où le statut vaut completed »
    emportait aussi les lignes sans statut. Le `coalesce` les rend au cas
    « retirer », puisqu'elles ne valent justement pas la valeur visée.
    """
    if action == "remove":
        return f"coalesce(not ({predicate}), true)"
    return predicate


@processor(
    "filter_value",
    "Filtrer sur une valeur",
    "Filtrage",
    "Garde, retire ou vide les lignes selon une valeur.",
    shortcut=True,
)
def p_filter_value(state: ColumnState, params: dict) -> StepSql:
    pred = _predicate(state, params)
    action = params.get("action", "remove")
    if action == "clear":
        col = params["column"]
        select = [
            (
                (f"case when {pred} then null else {q(c)} end", c)
                if c == col
                else (q(c), c)
            )
            for c in state.columns
        ]
        return StepSql(select=select, cte="vide")
    return StepSql(where=_keep_or_remove(pred, action), cte="filtre")


@processor(
    "filter_formula",
    "Filtrer avec une formule",
    "Filtrage",
    "Garde ou retire les lignes selon une condition SQL.",
)
def p_filter_formula(state: ColumnState, params: dict) -> StepSql:
    cond = check_predicate(params.get("condition", ""), "Filtrer avec une formule")
    action = params.get("action", "keep")
    return StepSql(where=_keep_or_remove(cond, action), cte="filtre")


@processor(
    "remove_empty",
    "Supprimer les lignes vides",
    "Filtrage",
    "Retire les lignes où une colonne est vide.",
)
def p_remove_empty(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Supprimer les lignes vides")
    return StepSql(where=filled_sql(q(col), state.type_of(col)), cte="non_vide")


# --- Nettoyage --------------------------------------------------------------


@processor(
    "fill_empty",
    "Remplir les cellules vides",
    "Nettoyage",
    "Remplace les valeurs vides par une valeur fixe.",
    shortcut=True,
)
def p_fill_empty(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Remplir les cellules vides")
    value = params.get("value", "")
    t = state.type_of(col)
    ref, replacement = q(col), typed_lit(value, t)
    # Sur une colonne qui ne porte pas de texte, « vide » se réduit à `null` :
    # `coalesce` dit exactement cela, et se lit mieux qu'un `case`.
    expr = (
        f"case when {empty_sql(ref, t)} then {replacement} else {ref} end"
        if _carries_text(t)
        else f"coalesce({ref}, {replacement})"
    )
    select = [(expr, c) if c == col else (q(c), c) for c in state.columns]
    return StepSql(select=select, cte="rempli")


@processor(
    "find_replace",
    "Rechercher & remplacer",
    "Nettoyage",
    "Remplace un texte par un autre dans une colonne.",
    shortcut=True,
)
def p_find_replace(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Rechercher & remplacer")
    find = params.get("find", "")
    repl = params.get("replace", "")
    expr = f"replace({as_text(q(col))}, {lit(find)}, {lit(repl)})"
    select = [(expr, c) if c == col else (q(c), c) for c in state.columns]
    state.types[col] = "VARCHAR"
    return StepSql(select=select, cte="remplace")


@processor(
    "text_transform",
    "Transformer le texte",
    "Nettoyage",
    "Majuscules, minuscules, ou suppression des espaces.",
    help_text="« Capitalisé » ne met en majuscule que la première lettre de la "
    "valeur, pas celle de chaque mot.",
)
def p_text(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Transformer le texte")
    mode = params.get("mode", "trim")
    text = as_text(q(col))
    # `initcap` n'existe ni dans DuckDB ni partout ailleurs : on l'écrit avec
    # des fonctions que tous les entrepôts connaissent. Ce n'en est pas
    # l'équivalent — `initcap` capitalise chaque mot, celui-ci la première
    # lettre de la valeur — et c'est ce que l'interface annonce. La longueur
    # est donnée explicitement : `substring(x, 2)` n'est pas accepté partout.
    rest = substring(text, 2, f"length({text})")
    capitalize = f"upper({substring(text, 1, 1)}) || lower({rest})"
    expr = {
        "upper": f"upper({text})",
        "lower": f"lower({text})",
        "trim": f"trim({text})",
        "capitalize": capitalize,
        "trim_lower": f"lower(trim({text}))",
    }.get(mode)
    if not expr:
        raise RecipeError(f"Transformation de texte inconnue : {mode}")
    select = [(expr, c) if c == col else (q(c), c) for c in state.columns]
    state.types[col] = "VARCHAR"
    return StepSql(select=select, cte="texte")


@processor(
    "change_type",
    "Changer le type",
    "Nettoyage",
    "Convertit une colonne dans un autre type.",
)
def p_change_type(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Changer le type")
    to = (params.get("to") or "").strip()
    # La virgule est de la partie : `decimal(18,2)` est proposé dans
    # l'interface, et `check_expr` refusera de toute façon ce qui n'est pas un
    # type que l'entrepôt comprend.
    if not re.match(r"^[A-Za-z][A-Za-z0-9_ (),]*$", to):
        raise RecipeError(f"Type invalide : {to}")
    expr = f"cast({q(col)} as {to})"
    check_expr(expr, "Changer le type")
    select = [(expr, c) if c == col else (q(c), c) for c in state.columns]
    state.types[col] = to.upper()
    return StepSql(select=select, cte="type")


@processor("round", "Arrondir", "Nombres", "Arrondit une colonne numérique.")
def p_round(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Arrondir")
    decimals = _int_param(
        params.get("decimals"), 0, "Arrondir", "Le nombre de décimales"
    )
    expr = f"round({q(col)}, {decimals})"
    select = [(expr, c) if c == col else (q(c), c) for c in state.columns]
    return StepSql(select=select, cte="arrondi")


# --- Dates ------------------------------------------------------------------


@processor(
    "parse_date",
    "Parser une date",
    "Dates",
    "Convertit un texte en vraie date.",
    shortcut=True,
    help_text="Format strptime : %d/%m/%Y, %Y-%m-%d %H:%M:%S…. Le format est "
    "traduit dans celui de l'entrepôt. Sur Redshift et PostgreSQL, une valeur "
    "illisible fait échouer l'exécution au lieu de rendre une case vide : "
    "filtrez-la avant.",
)
def p_parse_date(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Parser une date")
    fmt = params.get("format", "")
    into = params.get("into") or col
    validate_name(into, "colonne")
    if fmt:
        expr = parse_timestamp(as_text(q(col)), fmt)
    else:
        expr = safe_cast(q(col), "date")
    if into in state.columns:
        select = [(expr, c) if c == into else (q(c), c) for c in state.columns]
    else:
        select = [*_passthrough(state), (expr, into)]
        state.columns.append(into)
    state.types[into] = "DATE" if not fmt else "TIMESTAMP"
    return StepSql(select=select, cte="date_parsee")


@processor(
    "extract_date_parts",
    "Extraire les composants de date",
    "Dates",
    "Année, mois, jour, jour de la semaine.",
    help_text="Le numéro du jour de la semaine suit l'entrepôt, et n'est donc "
    "pas portable : 0 = dimanche sur DuckDB, Redshift et PostgreSQL, "
    "1 = lundi sur Athena, 1 = dimanche sur BigQuery. Sur Snowflake, "
    "« DAYOFWEEK » dépend du paramètre de session WEEK_START : à sa valeur "
    "par défaut (0), dimanche vaut 0 — et non 1. Si la numérotation compte "
    "pour vous, comparez plutôt le nom du jour, ou fixez la convention dans "
    "une colonne calculée.",
)
def p_date_parts(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Extraire les composants de date")
    parts = params.get("parts") or ["year", "month"]
    select = _passthrough(state)
    for p in parts:
        alias = f"{col}_{p}"
        select.append((date_part(p, q(col)), alias))
        state.columns.append(alias)
        state.types[alias] = "BIGINT"
    return StepSql(select=select, cte="composants_date")


# --- Lignes -----------------------------------------------------------------


@processor(
    "distinct_rows",
    "Dédoublonner",
    "Lignes",
    "Ne garde qu'une ligne par combinaison de valeurs.",
)
def p_distinct(state: ColumnState, params: dict) -> StepSql:
    return StepSql(distinct=True, cte="dedoublonne")


@processor("sort", "Trier", "Lignes", "Trie les lignes.")
def p_sort(state: ColumnState, params: dict) -> StepSql:
    col = state.require(params.get("column"), "Trier")
    direction = "desc" if params.get("descending") else "asc"
    return StepSql(order_by=f"{q(col)} {direction}", cte="trie")


# --- Lignes : dédoublonnage par clé et fenêtres --------------------------------


def _free_alias(base: str, taken: list[str]) -> str:
    """Un nom de colonne technique qui n'écrase aucune colonne existante."""
    name, n = f"_pliq_{base}", 1
    held = set(taken)
    while name in held:
        n += 1
        name = f"_pliq_{base}_{n}"
    return name


def _over(parts: list[str]) -> str:
    """La clause `over (…)`, sur plusieurs lignes dès qu'elle en vaut la peine.

    Une fenêtre tient rarement dans la largeur : partition, ordre et cadre mis
    bout à bout donnent une ligne qu'on ne relit pas. Le `.sql` reste la source
    de vérité — il doit se lire.
    """
    if not parts:
        return "over ()"
    if len(parts) == 1 and len(parts[0]) < 60:
        return f"over ({parts[0]})"
    body = "".join(f"\n                {p}" for p in parts)
    return f"over ({body}\n            )"


def _order_terms(state: ColumnState, params: dict, label: str) -> list[str]:
    """Le `order by` d'une fenêtre, valeurs vides toujours reléguées en dernier.

    `desc` remonte les NULL en tête sur DuckDB, PostgreSQL et Redshift : une
    ligne sans date passerait pour la plus récente, et c'est elle qu'on
    garderait. `nulls last` est du SQL standard, et les six entrepôts que
    l'atelier sait écrire le lisent.
    """
    terms = []
    for o in params.get("order_by") or []:
        col = state.require((o or {}).get("column"), label)
        direction = "desc" if (o or {}).get("descending") else "asc"
        terms.append(f"{q(col)} {direction} nulls last")
    return terms


@processor(
    "dedup_key",
    "Dédoublonner par clé",
    "Lignes",
    "Une seule ligne par clé — la plus récente, par exemple.",
    shortcut=True,
    help_text="« Dédoublonner » retire les lignes identiques sur toutes leurs "
    "colonnes ; ici, on choisit les colonnes qui font la clé métier, et un tri "
    "qui désigne la ligne à garder pour chaque clé. Les valeurs vides de la "
    "colonne de tri sont toujours reléguées en dernier : une date manquante ne "
    "doit pas passer pour la plus récente.",
)
def p_dedup_key(state: ColumnState, params: dict) -> StepSql:
    label = "Dédoublonner par clé"
    keys = [k for k in (params.get("keys") or []) if str(k).strip()]
    if not keys:
        raise RecipeError(
            f"{label} : choisissez au moins une colonne de clé. Sans clé, c'est "
            f"« Dédoublonner » qu'il vous faut — il retire les lignes identiques "
            f"partout."
        )
    for k in keys:
        state.require(k, label)

    sort_spec = _order_terms(state, params, label)
    if not sort_spec:
        raise RecipeError(
            f"{label} : dites sur quoi trier, pour qu'on sache quelle ligne "
            f"garder — la date de mise à jour la plus récente, par exemple. "
            f"Sans tri, la ligne retenue serait tirée au hasard, et pas "
            f"forcément la même d'une exécution à l'autre."
        )

    ties = params.get("ties") or "one"
    if ties not in ("one", "all"):
        raise RecipeError(f"{label} : règle de départage inconnue : {ties}")
    # `row_number` numérote sans jamais répéter : exactement une ligne par clé,
    # même quand le tri laisse deux lignes à égalité. `rank` donne 1 à toutes
    # les ex æquo, et les garde donc toutes — ce qui est parfois ce qu'on veut,
    # mais ne garantit plus l'unicité.
    fn = "row_number()" if ties == "one" else "rank()"

    rank = _free_alias("rang", state.columns)
    over = _over(
        [
            "partition by " + ", ".join(q(k) for k in keys),
            "order by " + ", ".join(sort_spec),
        ]
    )
    return StepSql(
        select=_passthrough(state),
        window=[(f"{fn} {over}", rank)],
        window_where=f"{q(rank)} = 1",
        cte="une_par_cle",
    )


WINDOW_FUNCTIONS = {
    "row_number": "Numéro dans la fenêtre",
    "rank": "Classement (ex æquo au même rang, rangs sautés)",
    "dense_rank": "Classement (ex æquo au même rang, rangs suivis)",
    "lag": "Valeur précédente",
    "lead": "Valeur suivante",
    "running_sum": "Cumul",
    "moving_avg": "Moyenne glissante",
    "sum": "Somme sur la fenêtre",
    "avg": "Moyenne sur la fenêtre",
    "min": "Minimum sur la fenêtre",
    "max": "Maximum sur la fenêtre",
    "count": "Nombre de lignes de la fenêtre",
}

# Celles qui portent sur une colonne, et pas sur la position dans la fenêtre.
WINDOW_NEEDS_COLUMN = (
    "lag",
    "lead",
    "running_sum",
    "moving_avg",
    "sum",
    "avg",
    "min",
    "max",
)
# Celles qui n'ont de sens que dans un ordre donné.
WINDOW_NEEDS_ORDER = (
    "row_number",
    "rank",
    "dense_rank",
    "lag",
    "lead",
    "running_sum",
    "moving_avg",
)
WINDOW_COUNTS = ("row_number", "rank", "dense_rank", "count")


@processor(
    "window_function",
    "Fonction de fenêtre",
    "Lignes",
    "Classement, cumul, valeur précédente, moyenne glissante.",
    help_text="La fenêtre découpe les lignes — « par client », « par client et "
    "par mois ». Les fonctions de position (classement, valeur précédente, "
    "cumul) ont besoin d'un ordre. Celles qui portent sur la fenêtre entière "
    "(somme, moyenne, minimum, maximum) n'en prennent pas : avec un ordre, "
    "l'entrepôt en ferait un cumul, c'est-à-dire un total différent sur chaque "
    "ligne.",
)
def p_window(state: ColumnState, params: dict) -> StepSql:
    label = "Fonction de fenêtre"
    fn = params.get("fn") or "row_number"
    if fn not in WINDOW_FUNCTIONS:
        raise RecipeError(f"{label} : fonction inconnue : {fn}")
    into = validate_name(params.get("into", ""), "colonne")

    col = ""
    if fn in WINDOW_NEEDS_COLUMN:
        col = state.require(params.get("column"), label)

    parts = []
    for p in params.get("partition_by") or []:
        parts.append(q(state.require(p, label)))

    sort_spec = _order_terms(state, params, label)
    if fn in WINDOW_NEEDS_ORDER and not sort_spec:
        raise RecipeError(
            f"« {WINDOW_FUNCTIONS[fn]} » a besoin d'un ordre : c'est lui qui dit "
            f"ce qui vient avant quoi. Ajoutez une colonne de tri."
        )
    if sort_spec and fn not in WINDOW_NEEDS_ORDER:
        raise RecipeError(
            f"« {WINDOW_FUNCTIONS[fn]} » porte sur la fenêtre entière et ne prend "
            f"pas d'ordre : avec un ordre, l'entrepôt n'additionnerait plus la "
            f"fenêtre mais tout ce qui précède — un total différent sur chaque "
            f"ligne. Retirez le tri, ou choisissez « Cumul »."
        )

    over = []
    if parts:
        over.append("partition by " + ", ".join(parts))
    if sort_spec:
        over.append("order by " + ", ".join(sort_spec))

    if fn in ("row_number", "rank", "dense_rank"):
        expr = f"{fn}()"
    elif fn in ("lag", "lead"):
        n = _positive(params.get("offset"), 1, label, "Le décalage")
        expr = f"{fn}({q(col)}, {n})"
    elif fn == "running_sum":
        expr = f"sum({q(col)})"
        over.append("rows between unbounded preceding and current row")
    elif fn == "moving_avg":
        n = _positive(params.get("window_rows"), 3, label, "La largeur de la moyenne")
        if n < 2:
            raise RecipeError(
                f"{label} : une moyenne glissante porte sur au moins deux lignes."
            )
        over.append(f"rows between {n - 1} preceding and current row")
        expr = f"avg({q(col)})"
    elif fn == "count":
        # `count(*)` compte des lignes ; `count(col)` ignorerait les vides.
        expr = "count(*)"
    else:
        expr = f"{fn}({q(col)})"

    expr = f"{expr} {_over(over)}"

    select = _passthrough(state)
    if into in state.columns:
        # Écraser une colonne dont la fenêtre elle-même se sert fait disparaître
        # la donnée qui a servi au calcul, et le script devient illisible.
        serving = set(params.get("partition_by") or []) | {
            (o or {}).get("column") for o in (params.get("order_by") or [])
        }
        if into == col or into in serving:
            raise RecipeError(
                f"{label} : « {into} » sert déjà au calcul de cette fenêtre. "
                f"Écrire le résultat dessus effacerait la donnée dont il vient. "
                f"Choisissez un autre nom de sortie."
            )
        select = [(expr, c) if c == into else (e, c) for e, c in select]
    else:
        select.append((expr, into))
        state.columns.append(into)
    state.types[into] = "BIGINT" if fn in WINDOW_COUNTS else None
    return StepSql(select=select, cte="fenetre")


def _int_param(raw_text: Any, default: int, label: str, what: str) -> int:
    """Un paramètre entier, ou une `RecipeError` qui nomme le coupable.

    `int()` à nu lève une `ValueError`, que les routes qui compilent n'attrapent
    pas : l'aperçu, la compilation et l'enregistrement répondaient 500 sans un
    mot. Un script visuel repris à la main dans `.pliq/recipes/` — ce que le
    produit annonce comme un usage normal — suffit à y arriver.

    Le signe n'est pas jugé ici : un arrondi à `-2` arrondit à la centaine, et
    c'est légitime. `_positive` ajoute la borne quand elle a un sens.
    """
    if raw_text in (None, ""):
        return default
    # `int(1.9)` rend 1 : un décimal saisi par erreur devenait une valeur
    # différente de celle qu'on lit à l'écran, sans un mot. Un entier écrit
    # « 2.0 » reste accepté — c'est bien un entier.
    if isinstance(raw_text, bool):
        raise RecipeError(f"{label} : {what} doit être un nombre entier.")
    if isinstance(raw_text, float):
        if not raw_text.is_integer():
            raise RecipeError(
                f"{label} : {what} doit être un nombre entier, et « {raw_text} » "
                f"n'en est pas un."
            )
        return int(raw_text)
    try:
        return int(str(raw_text).strip())
    except (TypeError, ValueError):
        raise RecipeError(f"{label} : {what} doit être un nombre entier.") from None


def _positive(raw_text: Any, default: int, label: str, what: str) -> int:
    n = _int_param(raw_text, default, label, what)
    if n < 1:
        raise RecipeError(f"{label} : {what} doit valoir au moins 1.")
    return n


# --- Colonnes : pivot et dépivotage -------------------------------------------


@processor(
    "unpivot",
    "Dépivoter (colonnes → lignes)",
    "Colonnes",
    "Replie plusieurs colonnes en deux : le nom, et la valeur.",
    help_text="Douze colonnes mensuelles deviennent douze lignes. L'écriture "
    "est une union — la seule qui se lise partout, `unpivot` n'existant ni sur "
    "PostgreSQL ni sur Redshift. Les colonnes repliées doivent porter le même "
    "genre de valeur : l'atelier refuse plutôt que d'empiler un texte sous un "
    "nombre.",
)
def p_unpivot(state: ColumnState, params: dict) -> StepSql:
    label = "Dépivoter"
    cols = [c for c in (params.get("columns") or []) if str(c).strip()]
    if len(cols) < 2:
        raise RecipeError(f"{label} : choisissez au moins deux colonnes à replier.")
    for c in cols:
        state.require(c, label)

    name_into = validate_name(params.get("name_into") or "variable", "colonne")
    value_into = validate_name(params.get("value_into") or "valeur", "colonne")
    if name_into == value_into:
        raise RecipeError(
            f"{label} : la colonne des noms et celle des valeurs ne peuvent pas "
            f"porter le même nom."
        )

    kept = [c for c in state.columns if c not in cols]
    for name in (name_into, value_into):
        if name in kept:
            raise RecipeError(
                f"{label} : « {name} » est déjà une colonne conservée. Choisissez "
                f"un autre nom de sortie, ou repliez cette colonne aussi."
            )

    families = {f for f in (type_family(state.type_of(c)) for c in cols) if f}
    if len(families) > 1:
        raise RecipeError(
            f"{label} : les colonnes choisies ne portent pas le même genre de "
            f"valeur ({', '.join(sorted(families))}). Repliées dans une seule "
            f"colonne, l'entrepôt refuserait la requête — ou pire, convertirait "
            f"en silence. Passez-les toutes dans le même type d'abord, avec "
            f"« Changer le type »."
        )

    def body(prev: str) -> str:
        blocks = []
        for c in cols:
            proj = [q(k) for k in kept]
            proj.append(f"{lit(c)} as {q(name_into)}")
            proj.append(f"{q(c)} as {q(value_into)}")
            blocks.append(
                "    select\n        "
                + ",\n        ".join(proj)
                + f"\n\n    from {prev}"
            )
        return "\n    union all\n".join(blocks)

    # Le type déclaré suit la première colonne repliée. Deux colonnes du même
    # genre mais de types différents — un INTEGER et un DOUBLE — sont unifiées
    # par l'entrepôt, et c'est lui qui a le dernier mot ; ce type-ci ne sert
    # qu'à proposer les étapes suivantes.
    value_type = state.type_of(cols[0])
    state.columns = [*kept, name_into, value_into]
    state.types = {c: t for c, t in state.types.items() if c in kept}
    state.types[name_into] = "VARCHAR"
    state.types[value_into] = value_type
    return StepSql(body=body, cte="deplie")


def pivot_alias(value: Any, prefix: str = "") -> str:
    """Le nom de colonne que produit une valeur pivotée.

    Les accents sont repliés plutôt que remplacés : « fév » doit donner
    `ca_fev`, pas `ca_f_v`. Un identifiant accentué serait légal sur certains
    entrepôts, mais pas sur tous, et il faudrait le citer partout.
    """
    flat = "".join(
        c
        for c in unicodedata.normalize("NFKD", str(value).strip().lower())
        if not unicodedata.combining(c)
    )
    slug = re.sub(r"[^a-z0-9_]+", "_", flat).strip("_")
    name = f"{prefix}{slug}"
    if not NAME_RE.match(name):
        raise RecipeError(
            f"Pivoter : « {value} » ne donne pas un nom de colonne valide "
            f"(« {name or '(vide)'} »). Ajoutez un préfixe — « mois_ », par "
            f"exemple."
        )
    return name


@processor(
    "pivot",
    "Pivoter (lignes → colonnes)",
    "Colonnes",
    "Déplie les valeurs d'une colonne en autant de colonnes.",
    help_text="Une colonne par valeur listée, une ligne par clé. Les valeurs "
    "sont données à la main, et c'est voulu : une requête ne peut pas inventer "
    "ses propres colonnes, et un modèle ne doit pas changer de schéma parce "
    "qu'une donnée nouvelle est apparue dans la source.",
)
def p_pivot(state: ColumnState, params: dict) -> StepSql:
    label = "Pivoter"
    keys = [k for k in (params.get("key_columns") or []) if str(k).strip()]
    for k in keys:
        state.require(k, label)
    name_col = state.require(params.get("name_column"), label)
    value_col = state.require(params.get("value_column"), label)
    if name_col == value_col:
        raise RecipeError(
            f"{label} : la colonne qui porte les noms et celle qui porte les "
            f"valeurs doivent être différentes."
        )

    values = [v for v in (params.get("values") or []) if str(v).strip() != ""]
    if not values:
        raise RecipeError(
            f"{label} : listez les valeurs de « {name_col} » à déplier en "
            f"colonnes. Une par colonne voulue."
        )

    fn = params.get("aggregate") or "max"
    if fn not in PIVOT_AGGREGATIONS:
        raise RecipeError(f"{label} : agrégation inconnue : {fn}")

    prefix = str(params.get("prefix") or "")
    if prefix and not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", prefix):
        raise RecipeError(f"{label} : préfixe invalide : « {prefix} ».")

    outputs_map: list[str] = []
    for v in values:
        alias = pivot_alias(v, prefix)
        if alias in keys or alias in outputs_map:
            raise RecipeError(
                f"{label} : deux colonnes de sortie s'appelleraient « {alias} ». "
                f"Changez le préfixe, ou retirez la valeur en double."
            )
        outputs_map.append(alias)

    name_type = state.type_of(name_col)

    def body(prev: str) -> str:
        proj = [q(k) for k in keys]
        for v, alias in zip(values, outputs_map, strict=True):
            # `count` compte des lignes : on compte alors une constante, sinon
            # les valeurs vides de la colonne mesurée manqueraient à l'appel.
            arg = "1" if fn == "count" else q(value_col)
            proj.append(
                f"{fn}(case when {q(name_col)} = {typed_lit(v, name_type)} "
                f"then {arg} end) as {q(alias)}"
            )
        out = "    select\n        " + ",\n        ".join(proj) + f"\n\n    from {prev}"
        if keys:
            out += "\n    group by " + ", ".join(str(i + 1) for i in range(len(keys)))
        return out

    value_type = "BIGINT" if fn == "count" else state.type_of(value_col)
    types = {c: t for c, t in state.types.items() if c in keys}
    state.columns = keys + outputs_map
    state.types = types
    for alias in outputs_map:
        state.types[alias] = value_type
    return StepSql(body=body, cte="pivote")

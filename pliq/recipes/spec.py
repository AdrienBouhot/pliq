"""La forme d'un script visuel, et ce qu'on lit dessus sans le compiler.

Le *sens* d'une recipe est l'affaire des compilateurs. Ici on ne juge que la
charpente — un objet, des listes d'objets — et les quelques lectures qui n'ont
besoin de rien d'autre : le nom des entrées, leur alias, le chemin du modèle.
"""

from __future__ import annotations

from typing import Any

import functools

from .dialect import current_dialect
from .errors import RecipeError
from .names import validate_name
from .sql import NAME_RE, _FORBIDDEN_ALIAS_RE
from .vocabulary import OBJECT_LIST_PARAMS, RECIPE_TYPES

# Le socle : les mots réservés du SQL standard. sqlglot porte la liste exacte
# de plusieurs entrepôts (`RESERVED_KEYWORDS`), mais elle est vide pour
# PostgreSQL, Snowflake et le dialecte générique — et un alias `order` y est
# tout aussi fatal. On réunit donc les deux : ce que sqlglot sait de
# l'entrepôt, et ce qu'aucun entrepôt n'accepte.
_CORE_RESERVED = frozenset("""
    all alter and any array as asc asymmetric authorization between both by case
    cast check collate column constraint create cross current_catalog current_date
    current_role current_schema current_time current_timestamp current_user default
    deferrable desc distinct do else end except exists false fetch filter for
    foreign freeze from full grant group having ilike in initially inner intersect
    into is isnull join lateral leading left like limit localtime localtimestamp
    natural not notnull null offset on only or order outer overlaps placing primary
    references returning right select session_user similar some symmetric table
    tablesample then to trailing true union unique user using values variadic verbose
    when where window with
    """.split())


@functools.lru_cache(maxsize=32)
def _reserved_words(dialect_name: str) -> frozenset:
    from sqlglot.dialects.dialect import Dialect

    try:
        engine = Dialect.get_or_raise(dialect_name or None)
        reserved = engine.generator_class.RESERVED_KEYWORDS
    except Exception:  # noqa: BLE001 — dialecte inconnu de sqlglot
        reserved = ()
    return _CORE_RESERVED | {str(m).lower() for m in reserved}


def _is_reserved(name: str, dialect_name: str) -> bool:
    """Ce nom peut-il servir d'alias de CTE sur cet entrepôt ?

    Les alias étaient nettoyés — caractères interdits remplacés — mais jamais
    confrontés aux mots réservés : une source parfaitement légitime nommée
    `order` produisait `with order as (...)`, c'est-à-dire une erreur de
    syntaxe, plusieurs écrans après la faute.
    """
    return name.lower() in _reserved_words(dialect_name)


def input_label(inp: dict) -> str:
    """L'entrée telle qu'on la nomme à l'écran, paquet et version compris.

    Jumeau d'`inputLabel` côté JavaScript. Deux entrées peuvent porter le même
    `ref` — un homonyme de paquet, deux versions d'un même modèle — et un
    message qui n'en dirait que le nom ne désignerait rien.
    """
    if inp.get("source_name"):
        return f"{inp.get('source_name')}.{inp.get('table')}"
    name = str(inp.get("ref") or "")
    version = inp.get("version")
    precisions = [
        str(x) for x in (inp.get("package"), f"v{version}" if version else "") if x
    ]
    return name + (f" ({', '.join(precisions)})" if precisions else "")


def input_alias(inp: dict, index: int) -> str:
    """Le nom du CTE d'une entrée : un identifiant, quoi qu'on lui donne à lire.

    Il se dérive souvent d'un nom que dbt porte, et un nom dbt n'est pas un
    identifiant : `order-items` est une table de source parfaitement valide, et
    `order-items as (...)` n'est pas du SQL. On le rabat donc sur un
    identifiant, au lieu de refuser l'entrée.
    """
    raw_text = (
        inp.get("alias") or inp.get("ref") or inp.get("table") or f"input_{index}"
    )
    text = _FORBIDDEN_ALIAS_RE.sub("_", str(raw_text).strip())
    if not text:
        return f"input_{index}"
    if not NAME_RE.match(text):
        text = f"_{text}"
    # Un mot réservé se préfixe plutôt que de se citer : un alias cité devrait
    # l'être *partout* où il est écrit — `from`, `join`, qualifications de
    # colonnes — et un seul oubli casse le fichier. Le préfixe, lui, donne un
    # identifiant sûr une fois pour toutes, et il se lit encore.
    return f"_{text}" if _is_reserved(text, current_dialect().sqlglot) else text


# ------------------------------------------------- forme de l'enveloppe


_TYPE_LABELS = {
    type(None): "une valeur vide",
    bool: "un booléen",
    int: "un nombre",
    float: "un nombre",
    str: "un texte",
    list: "une liste",
    dict: "un objet",
}


def _type_label(value: Any) -> str:
    """Le type d'une valeur, dit à quelqu'un qui ne lit pas de Python."""
    return _TYPE_LABELS.get(type(value), "autre chose")


# Les champs que le compilateur parcourt comme des listes d'objets. Il y lit
# `item.get(...)` sans se demander ce qu'est `item` : un `null` dans la liste y
# devient `None.get(...)`, c'est-à-dire une panne là où l'atelier n'avait qu'à
# refuser une saisie.
_OBJECT_LISTS = ("inputs", "steps")


# Les champs de texte que l'atelier lit avec `(spec.get(champ) or "").strip()`.
# Le `or ""` rattrape `null` et la chaîne vide, mais pas un nombre : `42.strip()`
# est une panne, là où un nom invalide n'avait qu'à être refusé.
_TEXT_FIELDS = ("name", "sql")


def _check_params_shape(params: dict, rtype: Any, rank: int) -> None:
    """L'étage sous l'enveloppe : les réglages que l'étape parcourt comme des objets.

    Le contrôle de la charpente s'arrêtait à « `params` est un objet », et le
    processeur prenait la suite en supposant le reste bien formé. Une étape de
    renommage dont les `renames` valaient `[null]` — ou `["colonne"]`, ce qui
    se saisit tout aussi facilement à la main — arrivait donc jusqu'à
    `r.get("from")` et rendait un 500 : une panne pour une saisie que l'atelier
    n'avait qu'à refuser.

    Ce qui est vérifié reste de la forme, pas du sens : une liste vide, une
    colonne inconnue, un nom illégal appartiennent au processeur, qui les
    refuse déjà en nommant l'étape et la raison.
    """
    for field in OBJECT_LIST_PARAMS.get(rtype, ()):
        value = params.get(field)
        # `params.get(champ) or []` du côté du processeur : un champ vide vaut
        # un champ absent, et ce contrôle ne doit pas être plus sévère que lui.
        if not value:
            continue
        or_ = f"« {field} » de l'étape n° {rank} (« {rtype} »)"
        if not isinstance(value, list):
            raise RecipeError(
                f"{or_} est une liste ; l'atelier a reçu {_type_label(value)}."
            )
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                raise RecipeError(
                    f"L'élément n° {i + 1} de {or_} est un objet ; "
                    f"l'atelier a reçu {_type_label(item)}."
                )


def check_spec_shape(spec: Any) -> dict:
    """La forme de l'enveloppe d'une recipe, avant que quiconque la lise.

    Ce contrôle ne porte que sur la *charpente* — un objet à la racine, des
    listes d'objets pour les entrées et les étapes. Le sens de ce qu'elles
    contiennent ne le regarde pas : une jointure sans clé, une mesure inconnue,
    une colonne absente sont l'affaire des compilateurs, qui les refusent déjà
    par une `RecipeError` assortie de leur contexte.

    Sans lui, une structure malformée traversait la validation — `spec: dict`
    accepte n'importe quel objet JSON — et ne s'arrêtait qu'au premier `.get()`
    sur un `None`, en 500. Or l'atelier s'interdit de rendre une panne pour un
    refus : c'est ce que dit déjà le gestionnaire de `RecipeError`.
    """
    if not isinstance(spec, dict):
        raise RecipeError(
            f"Un script de recipe est un objet ; l'atelier a reçu {_type_label(spec)}."
        )

    rtype = spec.get("type")
    # `RECIPE_TYPES.get(rtype)` sur une valeur non hachable est une panne, et
    # non le « type non géré » que le compilateur sait pourtant dire.
    if rtype is not None and not isinstance(rtype, str):
        raise RecipeError(
            f"Le type d'une recipe est un texte ; celui-ci est {_type_label(rtype)}."
        )

    for field in _TEXT_FIELDS:
        value = spec.get(field)
        # Un champ vide vaut un champ absent, comme partout ailleurs ici : le
        # `or ""` du lecteur en fait déjà une chaîne, et son propre refus dira
        # mieux que nous ce qui manque.
        if value and not isinstance(value, str):
            raise RecipeError(
                f"« {field} » est un texte ; l'atelier a reçu {_type_label(value)}."
            )

    for field in _OBJECT_LISTS:
        value = spec.get(field)
        # Le compilateur écrit `spec.get(champ) or []` partout : un champ vide
        # vaut un champ absent, et ce contrôle doit dire la même chose que lui.
        if not value:
            continue
        if not isinstance(value, list):
            raise RecipeError(
                f"« {field} » est une liste ; l'atelier a reçu {_type_label(value)}."
            )
        for n, item in enumerate(value):
            if not isinstance(item, dict):
                raise RecipeError(
                    f"L'élément n° {n + 1} de « {field} » est un objet ; "
                    f"l'atelier a reçu {_type_label(item)}."
                )

    # Les objets imbriqués que les compilateurs lisent avec `.get()` sans se
    # demander ce qu'ils sont. Une liste à la place d'un objet ne devenait
    # visible qu'au premier `.get()`, c'est-à-dire en 500 — alors que
    # l'atelier s'interdit de rendre une panne pour un refus.
    for field in ("output", "microbatch"):
        value = spec.get(field)
        if value and not isinstance(value, dict):
            raise RecipeError(
                f"« {field} » est un objet ; l'atelier a reçu {_type_label(value)}."
            )

    output = spec.get("output")
    if isinstance(output, dict):
        for field in ("incremental", "microbatch"):
            value = output.get(field)
            if value and not isinstance(value, dict):
                raise RecipeError(
                    f"« output.{field} » est un objet ; l'atelier a reçu "
                    f"{_type_label(value)}."
                )
        for field in ("unique_key", "tags", "columns"):
            value = output.get(field)
            if value and not isinstance(value, list):
                raise RecipeError(
                    f"« output.{field} » est une liste ; l'atelier a reçu "
                    f"{_type_label(value)}."
                )

    # Les étapes : `type` est un texte, `params` un objet. `params: ["oops"]`
    # traversait tout jusqu'au processeur, qui appelait `.get()` dessus.
    for n, step in enumerate(spec.get("steps") or []):
        t = step.get("type")
        if t is not None and not isinstance(t, str):
            raise RecipeError(
                f"Le type de l'étape n° {n + 1} est un texte ; l'atelier a reçu "
                f"{_type_label(t)}."
            )
        params = step.get("params")
        if params is not None and not isinstance(params, dict):
            raise RecipeError(
                f"Les réglages de l'étape n° {n + 1}"
                + (f" (« {t} »)" if isinstance(t, str) else "")
                + f" forment un objet ; l'atelier a reçu {_type_label(params)}."
            )
        if isinstance(params, dict):
            _check_params_shape(params, t, n + 1)

    # Les listes de configuration des recipes non-Prepare, lues de la même
    # façon par `compile_join`, `compile_group` et `compile_stack`.
    for field in ("joins", "select", "aggregations", "group_by"):
        value = spec.get(field)
        if not value:
            continue
        if not isinstance(value, list):
            raise RecipeError(
                f"« {field} » est une liste ; l'atelier a reçu {_type_label(value)}."
            )
        if field == "group_by":
            continue  # une liste de noms, pas d'objets
        for n, item in enumerate(value):
            if not isinstance(item, dict):
                raise RecipeError(
                    f"L'élément n° {n + 1} de « {field} » est un objet ; "
                    f"l'atelier a reçu {_type_label(item)}."
                )
    return spec


def check_max_inputs(spec: dict) -> None:
    """Le compte d'entrées passe avant leurs noms.

    Deux fois le même dataset sur une recipe qui n'en prend qu'un, le problème
    est qu'il y en a deux — pas qu'ils s'appellent pareil. Sans ce contrôle,
    c'est le doublon d'alias qui parlait le premier, et il expliquait la
    mauvaise chose.
    """
    meta = RECIPE_TYPES.get(spec.get("type", "prepare"))
    if not meta or str(meta.get("inputs", "")).endswith("+"):
        return
    cap = int(str(meta["inputs"]))
    input_entries = spec.get("inputs") or []
    if len(input_entries) > cap and cap == 1:
        raise RecipeError(
            f"Une recipe {meta['label']} prend exactement un dataset en entrée, "
            f"et il y en a {len(input_entries)}."
        )


def input_aliases(inputs: list[dict]) -> list[str]:
    """Les alias de toutes les entrées, dont on vérifie qu'ils sont distincts.

    Deux entrées qui portent le même alias, c'est deux CTE du même nom : une
    autojointure — le cas le plus banal qui soit — sortait un
    `Duplicate CTE name "orders"` de l'entrepôt, plusieurs écrans plus loin que
    la faute. Et les colonnes d'entrée, rangées par alias, se seraient écrasées
    avant même ça.

    On refuse plutôt que de renommer : `select`, `group_by` et les clés de
    jointure désignent leurs datasets *par cet alias*. Renommer la deuxième
    entrée en silence ferait pointer toutes ses colonnes sur la première, ce qui
    compile, s'exécute, et rend des lignes fausses. L'interface, elle, donne des
    alias distincts dès la création : ce contrôle est le filet, pas la porte.
    """
    aliases = [input_alias(inp, n) for n, inp in enumerate(inputs)]
    seen: dict[str, int] = {}
    for n, alias in enumerate(aliases):
        # Comparaison insensible à la casse : `A` et `a` passaient ce contrôle,
        # et l'entrepôt refusait ensuite les deux CTE comme des doublons. C'est
        # lui qui normalise les identifiants, pas Python.
        key = alias.casefold()
        if key in seen:
            precision = (
                ""
                if aliases[seen[key]] == alias
                else f" (« {aliases[seen[key]]} » et « {alias} » ne diffèrent que "
                f"par la casse, que l'entrepôt ignore)"
            )
            raise RecipeError(
                f"Les datasets n° {seen[key] + 1} "
                f"({input_label(inputs[seen[key]])}) "
                f"et n° {n + 1} ({input_label(inputs[n])}) s'appellent tous les "
                f"deux « {alias} »{precision}. Donnez-leur des noms différents : "
                f"c'est sous ce nom que les étapes les désignent."
            )
        seen[key] = n
    return aliases


def model_path(spec: dict, base: str = "models") -> str:
    """Où écrire le .sql d'une recipe, sous le `model-paths` du projet.

    `base` est un chemin relatif au projet, entier : dbt ne découvre que ce qui
    est sous ses `model-paths`, y compris quand ils sont imbriqués.
    """
    name = validate_name(spec.get("name", ""))
    layer = ((spec.get("output") or {}).get("layer") or "").strip()
    if layer:
        validate_name(layer, "couche")
        return f"{base}/{layer}/{name}.sql"
    return f"{base}/{name}.sql"


def free_cte(base: str, taken: set[str]) -> str:
    """Un nom de CTE encore libre, `taken` étant les noms déjà écrits.

    Les alias d'entrée sont des CTE comme les autres : sans ça, un dataset
    nommé « texte » suivi d'une transformation de texte produit deux CTE
    « texte », que l'entrepôt refuse.
    """
    # Insensible à la casse, comme l'entrepôt : un dataset « Texte » suivi
    # d'une étape de CTE « texte » donnait deux noms que Python distingue et
    # que DuckDB refuse.
    held = {t.casefold() for t in taken}
    name, n = base, 1
    while name.casefold() in held:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name

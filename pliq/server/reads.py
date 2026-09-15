"""Ce que les routes lisent du projet avant de répondre.

Les colonnes réelles d'une entrée, l'état des nœuds, le YAML qui
documente une recipe, l'endroit où son `.sql` doit être écrit.
"""

from __future__ import annotations


from fastapi import HTTPException

from .. import files, full_check
from .. import recipes as rcp
from ..warehouse import WarehouseError
from .workshop import Workshop
from .bodies import SaveRecipeBody
from .nodes import (
    _input_node,
    _layer_of,
    _model_file,
    _model_path,
)


def node_states(a: Workshop, service=None) -> dict[str, dict]:
    # `service` explicite pour un run déjà lancé : il doit rapporter l'état
    # du projet sur lequel il tourne, pas de celui qu'on vient d'ouvrir.
    service = service or a.svc
    # Copie avant parcours. Le thread de run ajoute une entrée à
    # `node_state` à chaque événement dbt, et il ne prend pas le
    # `project_lock` — que `GET /api/run` ne prend pas davantage. Itérer
    # directement dessus levait `RuntimeError: dictionary changed size during
    # iteration`, donc un 500, pendant un build : exactement le moment où l'on
    # regarde cet écran. `dict()` d'un dict est atomique sous le GIL.
    return {
        uid: {
            "status": st.status,
            "execution_time": st.execution_time,
            "message": st.message,
            "rows_affected": st.rows_affected,
        }
        for uid, st in dict(service.node_state).items()
    }


def recipe_specs(a: Workshop) -> dict[str, dict]:
    return rcp.list_recipes(a.settings.project_dir)


def input_columns(a: Workshop, spec: dict) -> dict[str, list[dict]]:
    """Colonnes réelles de chaque dataset d'entrée, lues dans l'entrepôt."""
    out: dict[str, list[dict]] = {}
    # La forme avant tout le reste, `spec.get` compris : `spec: dict` laisse
    # entrer n'importe quel objet JSON, et c'est ici que presque toutes les
    # routes de recipe lisent le leur pour la première fois.
    rcp.check_spec_shape(spec)
    input_entries = spec.get("inputs") or []
    # Les alias avant toute lecture : deux entrées homonymes rangeraient
    # leurs colonnes sous la même clé, et la seconde effacerait la première.
    try:
        rcp.check_max_inputs(spec)
        aliases = rcp.input_aliases(input_entries)
    except rcp.RecipeError as exc:
        raise HTTPException(400, str(exc)) from None
    for alias, inp in zip(aliases, input_entries, strict=True):
        try:
            out[alias] = a.wh.columns_of_sql(f"select * from {rcp.ref_sql(inp)}")
        except (WarehouseError, rcp.RecipeError) as exc:
            raise HTTPException(
                400,
                f"Colonnes de « {rcp.input_label(inp)} » illisibles : {exc}",
            ) from None
    return out


def _microbatch_warnings(a: Workshop, spec: dict) -> list[str]:
    """Ce que dbt accepte mais qui coûte cher, et qu'il faut donc dire.

    Un modèle microbatch dont aucune entrée ne déclare `event_time` se
    construit sans erreur — mais dbt relit alors la source *entière* pour
    chaque tranche, ce qui est exactement l'inverse de ce qu'on cherchait.
    dbt n'en émet qu'un événement de log, invisible depuis l'atelier.
    """
    cfg = rcp.microbatch(spec.get("output") or {})
    if not cfg:
        return []
    names = []
    for inp in spec.get("inputs") or []:
        node = _input_node(a, inp)
        if node is not None and getattr(
            getattr(node, "config", None), "event_time", None
        ):
            return []
        names.append(rcp.input_label(inp))
    if not names:
        return []
    what = ", ".join(f"« {n} »" for n in names)
    return [
        f"Aucune entrée ne déclare de colonne de temps : dbt lira {what} "
        f"en entier pour chaque tranche, au lieu de n'en lire que la "
        f"fenêtre. Ouvrez « Documenter & tester » sur l'entrée et donnez-y "
        f"sa colonne de temps."
    ]


def _full_profile(a: Workshop, sql: str, columns: list[dict]) -> dict:
    """Agrège sur la table entière ce que la grille ne devine que sur 200 lignes."""
    measures, plan, discarded = full_check.profile_measures(columns)
    try:
        data = a.wh.query(full_check.aggregate_over(sql, measures), 1)
    except WarehouseError as exc:
        raise HTTPException(400, str(exc)) from None
    if not data["rows"]:
        raise HTTPException(400, "Le contrôle complet n'a rien retourné.")
    values_list = dict(
        zip(
            [c["name"].lower() for c in data["columns"]],
            data["rows"][0],
            strict=True,
        )
    )
    out = full_check.read_profile(plan, values_list)
    out["skipped"] = discarded
    out["column_limit"] = full_check.PROFILE_MAX_COLUMNS
    return out


def _recipe_doc(a: Workshop, name: str, schema_path: str) -> dict:
    """La doc du modèle, lue là où dbt la lit : le YAML fait foi.

    L'éditeur de recipe et la fiche dataset écrivent alors au même endroit,
    et la recipe ne porte plus de copie qui puisse dater.
    """
    entry = files.read_doc(a.settings, schema_path, name)
    columns = []
    for col in entry.get("columns") or []:
        if not isinstance(col, dict) or not col.get("name"):
            continue
        columns.append(
            {
                "name": str(col["name"]),
                "description": str(col.get("description") or ""),
                "tests": files.parse_column_tests(col),
            }
        )
    return {
        "description": str(entry.get("description") or ""),
        "columns": columns,
    }


def _save_target(
    a: Workshop, body: SaveRecipeBody, name: str, spec: dict
) -> tuple[str, str | None]:
    """(fichier à écrire, fichier à retirer) pour l'enregistrement d'une recipe.

    La lecture retrouve le modèle par le manifeste — donc là où il est
    vraiment. L'écriture, elle, recalculait son chemin depuis la couche
    notée dans la recipe : un modèle déplacé à la main de `staging` vers
    `marts` se voyait recréé dans `staging`, et dbt refusait ensuite deux
    modèles du même nom.

    Une modification suit donc le fichier réel. Sauf quand c'est la couche
    de la recipe qui a changé : là, le déplacement est demandé, et l'ancien
    fichier part avec.
    """
    wanted = _model_path(a, spec)
    if body.is_new:
        return wanted, None

    model_file = _model_file(a, name, spec)
    if not model_file or model_file == wanted:
        return wanted, None

    before = rcp.load_recipe(a.settings.project_dir, name) or {}
    if _layer_of(a, before) != _layer_of(a, spec):
        return wanted, model_file
    return model_file, None

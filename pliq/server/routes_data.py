"""L'entrepôt, les sources, les couches, et les contrôles complets."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .. import datasets, files, full_check, graph, profiling
from .. import recipes as rcp
from ..warehouse import WarehouseError
from .workshop import Workshop
from .bodies import FullProfileBody, SampleBody, SourceBody, SuggestBody
from .reads import _full_profile, input_columns
from .nodes import _columns_of, get_node, nodes_by_id


def mount(app: FastAPI, a: Workshop) -> None:
    @app.post("/api/profile/full")
    @a.serialized
    def profile_full(body: FullProfileBody) -> dict:
        """Le contrôle complet : compté sur tout, et annoncé comme tel.

        Sur une recipe Préparer, il profile aussi l'entrée : c'est la
        comparaison des deux qui donne « 12 % des lignes supprimées, 340 dates
        devenues nulles » — deux chiffres qu'un échantillon ne peut pas donner.
        """
        if body.uid:
            node = get_node(a, body.uid)
            relation = getattr(node, "relation_name", None)
            if not relation:
                raise HTTPException(
                    400,
                    "Ce dataset n'existe pas encore dans l'entrepôt : "
                    "lancez « dbt build » pour le construire.",
                )
            return {
                "scope": "complet",
                "output": _full_profile(
                    a, f"select * from {relation}", _columns_of(a, node)
                ),
            }

        spec = body.spec
        if not spec:
            raise HTTPException(400, "Rien à contrôler.")
        cols = input_columns(a, spec)
        try:
            sql = rcp.compile_recipe(spec, cols)
        except rcp.RecipeError as exc:
            raise HTTPException(400, str(exc)) from None

        try:
            output = a.wh.columns_of_sql(sql)
        except WarehouseError as exc:
            raise HTTPException(400, str(exc)) from None
        out = {"scope": "complet", "output": _full_profile(a, sql, output)}

        # L'avant/après ne veut dire quelque chose que sur une entrée unique :
        # rapporter « 12 % de lignes en moins » sur une jointure ou une union
        # laisserait croire qu'il y a une entrée de référence, alors qu'il y en
        # a plusieurs, et que le compte n'a pas de sens.
        input_entries = spec.get("inputs") or []
        if spec.get("type", "prepare") == "prepare" and len(input_entries) == 1:
            alias = rcp.input_alias(input_entries[0], 0)
            out["input"] = _full_profile(
                a,
                f"select * from {rcp.ref_sql(input_entries[0])}",
                cols.get(alias) or [],
            )
            out["changes"] = full_check.profile_delta(out["input"], out["output"])
        return out

    @app.post("/api/test/{uid}/failures")
    @a.serialized
    def test_failures(uid: str, body: SampleBody) -> dict:
        """Les lignes qui font échouer un test.

        dbt compile chaque test en une requête qui *sélectionne les échecs* :
        la rejouer montre exactement les lignes fautives. Cliquer un test rouge
        et voir les données, c'est la moitié du chemin entre « ça a échoué » et
        « voilà quoi corriger ».
        """
        node = nodes_by_id(a).get(uid)
        if node is None or graph.kind_of(node) != "test":
            raise HTTPException(404, f"Test inconnu : {uid}")

        sql = a.svc.compiled_test_sql(node)
        if not sql or not sql.strip():
            raise HTTPException(
                400,
                "Ce test n'a pas encore été compilé par dbt. Lancez « test » ou "
                "« compile » depuis la barre du haut, puis réessayez.",
            )
        try:
            data = a.wh.query(sql, body.limit)
        except WarehouseError as exc:
            raise HTTPException(400, str(exc)) from None

        return {
            "test": getattr(node, "name", uid),
            "column": getattr(node, "column_name", None),
            "columns": profiling.profile(data["columns"], data["rows"]),
            "rows": data["rows"],
            "sql": sql.strip(),
            "state": (
                a.svc.node_state.get(uid).status if uid in a.svc.node_state else "idle"
            ),
        }

    # ------------------------------------------------------ nouveaux datasets

    @app.get("/api/warehouse/tables")
    @a.serialized
    def warehouse_tables() -> dict:
        try:
            return datasets.inventory(a.settings, a.wh, a.svc.manifest())
        except WarehouseError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/datasets/source")
    @a.serialized
    def add_source(body: SourceBody) -> dict:
        a.reject_during_run("cette déclaration")
        # Le manifeste dit la base que dbt donne déjà aux tables du groupe
        # visé — celle de la cible quand le YAML n'en nomme aucune. Sans lui,
        # on ne saurait pas qu'ajouter là une table d'une autre base revient à
        # la faire lire ailleurs. S'il manque, la déclaration reste possible :
        # le contrôle retombe alors sur ce qui est écrit dans le YAML.
        try:
            manifest = a.svc.manifest()
        except Exception:  # noqa: BLE001
            manifest = None
        try:
            out = datasets.declare_source(
                a.settings,
                source_name=body.source_name,
                schema=body.schema_name,
                tables=body.tables,
                description=body.description,
                database=body.database,
                manifest=manifest,
            )
        except (datasets.DatasetError, files.FileError) as exc:
            raise HTTPException(400, str(exc)) from None

        try:
            a.svc.parse(force=True)
            if a.svc.manifest_error:
                out["parse_error"] = a.svc.manifest_error
        except Exception as exc:  # noqa: BLE001
            out["parse_error"] = str(exc)
        a.hub.publish({"type": "flow_changed"})
        return out

    # -------------------------------------------------------------- couches

    @app.get("/api/layers")
    def get_layers() -> dict:
        return {"layers": datasets.layers(a.settings)}

    @app.post("/api/layers/suggest")
    @a.serialized
    def suggest_layer(body: SuggestBody) -> dict:
        out = datasets.suggest_layer(a.settings, a.svc.manifest(), body.inputs)
        out["layers"] = datasets.layers(a.settings)
        return out

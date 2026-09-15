"""Les recipes : les compiler, les prévisualiser, les écrire."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from .. import files, graph, join_diagnostics, profiling
from .. import recipes as rcp
from ..warehouse import WarehouseError
from .workshop import Workshop
from .bodies import (
    DeleteRecipeBody,
    RecipeBody,
    RenameRecipeBody,
    SaveRecipeBody,
    SpecBody,
)
from .reads import (
    _microbatch_warnings,
    _recipe_doc,
    _save_target,
    input_columns,
)
from .nodes import (
    _model_path,
    _recipe_schema_path,
    _downstream_models,
    _mentions,
    _model_file,
    _model_node,
    _versioned_model,
    _name_taken,
    _nothing_to_rename,
    _check_identity,
)
from .versions import _check_save_allowed, _recipe_base


def mount(app: FastAPI, a: Workshop) -> None:
    # ------------------------------------------------------------- recipes

    @app.get("/api/processors")
    def processors() -> dict:
        lib = rcp.library()
        categories: list[str] = []
        for p in lib:
            if p["category"] not in categories:
                categories.append(p["category"])
        return {
            "processors": lib,
            "categories": categories,
            "recipe_types": [{"key": k, **v} for k, v in rcp.RECIPE_TYPES.items()],
            "materializations": list(rcp.MATERIALIZATIONS),
            # Les stratégies *que cet entrepôt sait exécuter*, et non la liste
            # complète : les proposer toutes laissait configurer une recipe
            # normalement, puis échouer au `dbt build` sur une stratégie que
            # l'adaptateur ne connaît pas. Le compilateur pose le même refus,
            # côté serveur ; l'écran n'a pas à offrir ce qu'il refusera.
            "incremental_strategies": list(rcp.strategies_available()),
            "default_incremental_strategy": rcp.default_strategy(),
            "all_incremental_strategies": list(rcp.INCREMENTAL_STRATEGIES),
            "strategies_needing_key": list(rcp.STRATEGIES_NEEDING_KEY),
            "on_schema_change": list(rcp.ON_SCHEMA_CHANGE),
            "batch_sizes": list(rcp.BATCH_SIZES),
            "lookback_units": list(rcp.LOOKBACK_UNITS),
            # Les fonctions de fenêtre, et celles qui ont besoin d'une colonne
            # ou d'un ordre : l'interface n'affiche que les champs utiles, et
            # la liste doit venir d'ici pour ne pas diverger.
            "window_functions": [
                {"key": k, "label": v} for k, v in rcp.WINDOW_FUNCTIONS.items()
            ],
            "window_needs_column": list(rcp.WINDOW_NEEDS_COLUMN),
            "window_needs_order": list(rcp.WINDOW_NEEDS_ORDER),
            # Clé *et* libellé, pour les deux listes qui suivent. L'interface
            # en portait sa propre copie — même clés, mêmes libellés, son
            # propre ordre — et un test se chargeait de vérifier que les deux
            # restaient d'accord. Un test qui surveille une duplication ne la
            # remplace pas : il en fait une dette qu'on entretient.
            "pivot_aggregations": [
                {"key": k, "label": v} for k, v in rcp.PIVOT_AGGREGATIONS.items()
            ],
            # Les opérateurs du filtre « sur une valeur ». Le même libellé sert
            # au menu déroulant et à la phrase de la carte d'étape : ils ne
            # peuvent plus se contredire.
            # Quels opérateurs prennent une valeur, et combien. L'écran
            # n'offrait qu'un champ « Valeurs (séparées par des virgules) »
            # pour les treize, alors que onze d'entre eux n'en comparent
            # qu'une : la carte affichait toute la liste, le SQL n'en gardait
            # que la première, et rien ne le disait. Le compilateur refuse
            # désormais la seconde valeur ; l'écran n'a plus à l'offrir.
            "single_value_operators": list(rcp.SINGLE_VALUE_OPERATORS),
            "no_value_operators": list(rcp.NO_VALUE_OPERATORS),
            "operators": [
                {"key": k, "label": v} for k, v in rcp.FILTER_OPERATORS.items()
            ],
            # Les types proposés par « Changer le type » nomment ceux de
            # l'entrepôt ouvert : `varchar` n'existe pas sur BigQuery, `double`
            # ni sur PostgreSQL ni sur Redshift.
            "types": rcp.types_offered(),
        }

    @app.get("/api/refs")
    @a.serialized
    def refs() -> dict:
        m = a.svc.manifest()
        out = []
        for node in list(m.nodes.values()) + list(m.sources.values()):
            kind = graph.kind_of(node)
            if kind not in ("model", "seed", "source", "snapshot"):
                continue
            if kind == "source":
                out.append(
                    {
                        "kind": "source",
                        "label": f"{node.source_name}.{node.name}",
                        "source_name": node.source_name,
                        "table": node.name,
                        "id": node.unique_id,
                    }
                )
            else:
                # Un nom ne suffit pas à désigner un dataset : deux paquets
                # peuvent déclarer `orders`, et un modèle versionné porte le
                # même nom à toutes ses versions. Le libellé doit les
                # distinguer — c'est ce que l'utilisateur lit pour choisir —
                # et l'entrée doit emporter de quoi écrire le bon `ref()`.
                local = graph.belongs_to_project(node, a.settings.project_name)
                package = "" if local else str(getattr(node, "package_name", "") or "")
                version = getattr(node, "version", None)
                input_entry = {
                    "kind": "ref",
                    "label": node.name,
                    "ref": node.name,
                    "package": package,
                    "version": "" if version is None else str(version),
                    "resource": kind,
                    "id": node.unique_id,
                }
                precisions = [
                    x for x in (package, f"v{version}" if version else "") if x
                ]
                if precisions:
                    input_entry["label"] += f" ({', '.join(precisions)})"
                out.append(input_entry)
        out.sort(key=lambda r: r["label"])
        return {"refs": out}

    @app.get("/api/recipe/{name}")
    @a.serialized
    def get_recipe(name: str, model_uid: str | None = None) -> dict:
        # Lire aussi : ouvrir la recipe d'un modèle de paquet chargeait le
        # script visuel du modèle local homonyme, qui n'a rien à voir avec lui.
        _check_identity(a, name, model_uid)
        spec = rcp.load_recipe(a.settings.project_dir, name)
        if spec is None:
            # Absent et illisible ne se disent pas pareil : « pas de recipe »
            # invite à en créer une par-dessus celle qu'on n'a pas su lire.
            reason = rcp.readonly_reason(a.settings.project_dir, name)
            if reason:
                raise HTTPException(400, reason)
            raise HTTPException(404, f"Aucun script visuel pour « {name} ».")
        # Même raison qu'à l'enregistrement : un nom ne désigne pas une version.
        _versioned_model(a, name, spec, "l'ouverture d'une recipe visuelle")
        schema_path = _recipe_schema_path(a, name, spec)
        # La doc vient du YAML dbt, jamais de la recipe : une copie qu'on lirait
        # ici réappliquerait à l'enregistrement ce que la fiche dataset a changé.
        spec.setdefault("output", {}).update(_recipe_doc(a, name, schema_path))
        return {
            "spec": spec,
            "base": _recipe_base(a, name, _model_file(a, name, spec), schema_path),
        }

    @app.post("/api/recipe/columns")
    @a.serialized
    def recipe_columns(body: RecipeBody) -> dict:
        return {"columns": input_columns(a, body.spec)}

    @app.post("/api/recipe/compile")
    @a.serialized
    def recipe_compile(body: RecipeBody) -> dict:
        cols = input_columns(a, body.spec)
        try:
            sql = rcp.compile_recipe(body.spec, cols)
        except rcp.RecipeError as exc:
            raise HTTPException(400, str(exc)) from None
        path = _model_path(a, body.spec)
        return {
            "sql": sql,
            "path": path,
            "exists": (a.settings.project_dir / path).exists(),
        }

    @app.post("/api/recipe/preview")
    @a.serialized
    def recipe_preview(body: RecipeBody) -> dict:
        spec = body.spec
        cols = input_columns(a, spec)
        deltas: list[dict] = []

        try:
            if spec.get("type", "prepare") == "prepare":
                sql, _state, deltas = rcp.compile_prepare(spec, cols, upto=body.upto)
            else:
                sql = rcp.COMPILERS[spec.get("type")](spec, cols)
        except rcp.RecipeError as exc:
            raise HTTPException(400, str(exc)) from None
        except KeyError:
            raise HTTPException(
                400, f"Type de recipe non géré : {spec.get('type')}"
            ) from None

        try:
            data = a.wh.query(sql, body.limit)
        except WarehouseError as exc:
            raise HTTPException(400, str(exc)) from None

        profiled = profiling.profile(data["columns"], data["rows"])
        return {
            "columns": profiled,
            "rows": data["rows"],
            "sql": sql,
            "deltas": deltas,
            "warnings": _microbatch_warnings(a, spec),
            "suggestions": {c["name"]: profiling.suggestions(c) for c in profiled},
        }

    @app.post("/api/recipe/diagnose")
    @a.serialized
    def recipe_diagnose(body: SpecBody) -> dict:
        """Ce qu'une jointure fait vraiment aux lignes — sur les tables entières.

        Volontairement à la demande : chaque mesure est un agrégat sur toute la
        table, et l'atelier ne va pas facturer un `count(*)` complet à chaque
        frappe. C'est aussi pourquoi le résultat dit « contrôle complet » et
        non « échantillon » : les deux ne s'équivalent pas, et un contrôle
        d'unicité sur deux cents lignes n'en est pas un.
        """
        # Cette route lit le `type` avant d'appeler `input_columns` : la forme
        # doit donc être établie ici, et non au moment de lire les colonnes.
        spec = rcp.check_spec_shape(body.spec)
        if spec.get("type") != "join":
            raise HTTPException(400, "Le diagnostic porte sur une recipe Joindre.")
        cols = input_columns(a, spec)
        try:
            sql, plan = join_diagnostics.diagnose_join_sql(spec, cols)
        except rcp.RecipeError as exc:
            raise HTTPException(400, str(exc)) from None
        try:
            data = a.wh.query(sql, 1)
        except WarehouseError as exc:
            raise HTTPException(400, str(exc)) from None
        if not data["rows"]:
            raise HTTPException(400, "Le diagnostic n'a rien retourné.")
        values_list = dict(
            zip(
                [c["name"].lower() for c in data["columns"]],
                data["rows"][0],
                strict=True,
            )
        )
        return {
            "findings": join_diagnostics.join_diagnosis(plan, values_list),
            "scope": "complet",
        }

    @app.post("/api/recipe/impact")
    @a.serialized
    def recipe_impact(body: SpecBody) -> dict:
        """Les colonnes que cet enregistrement ferait disparaître, et qui s'en sert.

        L'atelier connaît le DAG et les scripts visuels : il peut dire avant
        d'écrire qu'un renommage va casser trois recipes en aval. Sans ça, on
        l'apprend au `dbt build`, une fois le fichier déjà remplacé.

        Une colonne qui disparaît parce qu'elle est *renommée* vient avec son
        nouveau nom (`renamed_to`) : « renommée en total » se répare autrement
        que « supprimée », et l'aval a besoin de savoir quoi écrire à la place.

        L'atelier ne le réécrit pas lui-même : la substitution était textuelle
        et ignorait la portée SQL — les deux branches d'un `union all` ont
        chacune la leur — si bien qu'elle renommait des colonnes qui venaient
        d'ailleurs et cassait le modèle. Avertir avant d'écrire est ce que
        cette route sait faire de juste.
        """
        # Idem : le nom se lit avant toute lecture de colonnes.
        spec = rcp.check_spec_shape(body.spec)
        try:
            name = rcp.validate_name(spec.get("name", ""))
        except rcp.RecipeError:
            return {"impacts": []}

        before = rcp.load_recipe(a.settings.project_dir, name)
        if before is None:
            return {"impacts": []}  # rien d'enregistré : rien à casser

        cols = input_columns(a, spec)
        before_cols = rcp.output_columns(before, cols)
        after_cols = rcp.output_columns(spec, cols)
        if before_cols is None or after_cols is None:
            return {"impacts": []}

        remaining = set(after_cols)
        gone = [c for c in before_cols if c not in remaining]
        if not gone:
            return {"impacts": []}

        renamed = rcp.renamed_columns(before, spec, cols)
        downstream = _downstream_models(a, name)
        impacts = []
        for column in gone:
            usages = [
                {"model": getattr(n, "name", ""), "where": or_}
                for n in downstream
                if (or_ := _mentions(a, n, column))
            ]
            if usages:
                impacts.append(
                    {
                        "column": column,
                        "used_by": usages,
                        "renamed_to": renamed.get(column),
                    }
                )
        return {"impacts": impacts, "dropped": gone}

    @app.post("/api/recipe/save")
    @a.serialized
    def recipe_save(body: SaveRecipeBody) -> dict:
        a.reject_during_run("cet enregistrement")
        spec = body.spec
        cols = input_columns(a, spec)
        try:
            name = rcp.validate_name(spec.get("name", ""))
            sql = rcp.compile_recipe(spec, cols)
        except rcp.RecipeError as exc:
            raise HTTPException(400, str(exc)) from None

        _check_identity(a, name, body.model_uid)
        # Une recipe s'adresse par *nom*, et un modèle versionné en porte
        # plusieurs sous le même : `_model_node` rendait alors le premier
        # rencontré, et un enregistrement demandé sur `m.v2` réécrivait
        # `models/m_v1.sql` en répondant 200. La suppression et le renommage
        # posaient déjà ce refus ; l'enregistrement — celui qui écrit — ne
        # l'avait pas. Tant que les scripts visuels ne sont pas rangés par
        # version, mieux vaut refuser que d'écrire dans la mauvaise.
        _versioned_model(a, name, spec, "cet enregistrement")

        out = spec.get("output") or {}
        desc = out.get("description", "")
        col_docs = out.get("columns") or []
        path, to_drop = _save_target(a, body, name, spec)
        schema_path = _recipe_schema_path(a, name, spec)

        # Tout ce qui peut être refusé l'est avant la première écriture : un
        # test incomplet ne doit pas laisser le SQL neuf à côté de l'ancien YAML.
        try:
            files.validate_columns(col_docs)
            model_file = files.safe_path(a.settings, path)
            schema_file = files.safe_path(a.settings, schema_path)
            old_file = files.safe_path(a.settings, to_drop) if to_drop else None
        except files.FileError as exc:
            raise HTTPException(400, str(exc)) from None

        _check_save_allowed(
            a, body, name, path, model_file, schema_path, cols, old_file
        )

        try:
            recipe_file = rcp.recipe_file(a.settings.project_dir, name)
            # Ou les trois fichiers changent, ou aucun : une erreur tardive
            # laissait le projet entre deux versions, sans rien pour le dire.
            touches = [model_file, recipe_file, schema_file]
            if old_file is not None:
                touches.append(old_file)
            with files.all_or_nothing(touches):
                files.write_text(a.settings, path, sql)
                # Un modèle qui change de couche est *déplacé*, pas recopié :
                # deux fichiers du même nom, et dbt refuse de parser le projet.
                if old_file is not None:
                    old_file.unlink(missing_ok=True)
                recipe_rel = rcp.save_recipe(a.settings.project_dir, spec)
                # Ne pas créer un schema.yml qui ne contiendrait qu'un nom :
                # tant que rien n'est documenté ni testé, il n'y a rien à
                # écrire.
                #
                # Mais une entrée déjà sur le disque doit être réécrite même
                # vidée, sinon retirer la dernière description et le dernier
                # test répond « enregistré » pendant que dbt continue de faire
                # tourner les anciens tests.
                documented = bool(desc) or any(
                    c.get("description") or c.get("tests") for c in col_docs
                )
                existing = bool(files.read_doc(a.settings, schema_path, name))
                if documented or existing:
                    files.upsert_doc(
                        a.settings,
                        schema_path,
                        name,
                        kind="model",
                        description=desc,
                        columns=col_docs,
                    )
        except (files.FileError, rcp.RecipeError, OSError) as exc:
            raise HTTPException(400, f"Écriture impossible : {exc}") from None

        # `parse()` ne lève que s'il n'a aucun manifest à rendre : quand un
        # ancien traîne, l'échec n'est visible que dans `manifest_error`.
        parse_error = None
        try:
            a.svc.parse(force=True)
            parse_error = a.svc.manifest_error
        except Exception as exc:  # noqa: BLE001
            parse_error = str(exc)

        # `_model_node` et pas le premier homonyme du manifeste : celui-ci porte
        # aussi les nœuds des paquets, et de tous les types. L'éditeur retient
        # cette identité et la rend à ses écritures suivantes — une identité de
        # paquet lui faisait refuser sa propre recipe au deuxième
        # enregistrement, avec un message parlant d'un paquet qu'il n'a jamais
        # visé. `recipe_rename` passait déjà par là.
        uid = None
        if parse_error is None:
            node = _model_node(a, name)
            uid = getattr(node, "unique_id", None) if node is not None else None

        a.hub.publish({"type": "flow_changed"})

        if body.run and parse_error is None:
            a.launch_run("build", name)

        return {
            "saved": True,
            "path": path,
            "recipe_path": recipe_rel,
            "schema_path": schema_path,
            "unique_id": uid,
            "parse_error": parse_error,
            # Les empreintes de ce qu'on vient d'écrire : l'éditeur les garde
            # pour que la sauvegarde suivante ne se croie pas en conflit avec
            # celle-ci.
            "base": _recipe_base(a, name, path, schema_path),
        }

    @app.post("/api/recipe/delete")
    @a.serialized
    def recipe_delete(body: DeleteRecipeBody) -> dict:
        """Efface un script visuel, et si on le demande le modèle qu'il écrit.

        Les deux sont séparables : oublier la recipe en gardant le SQL rend le
        modèle à la main, tandis que tout supprimer le retire du Flow.
        """
        a.reject_during_run("cette suppression")
        try:
            name = rcp.validate_name(body.name)
        except rcp.RecipeError as exc:
            raise HTTPException(400, str(exc)) from None

        # Avant tout le reste : le nom ne suffit pas à dire quel modèle est visé.
        _check_identity(a, name, body.model_uid)

        spec = rcp.load_recipe(a.settings.project_dir, name)
        if body.delete_model:
            _versioned_model(a, name, spec, "la suppression")
        model_rel = _model_file(a, name, spec)
        # Résolu avant la suppression : `patch_path` se lit dans le manifeste,
        # et le manifeste ne connaîtra plus ce modèle une fois le .sql effacé.
        schema_path = _recipe_schema_path(a, name, spec)
        if spec is None and not model_rel:
            raise HTTPException(404, f"Rien à supprimer pour « {name} ».")

        # Ce qu'on touchera, résolu et vérifié *avant* la première suppression.
        # Effacer la recipe, puis le SQL, puis la doc laissait le projet à
        # mi-chemin dès que le dernier geste échouait — un schema.yml illisible
        # suffisait, et ce qui était déjà parti ne revenait pas.
        model_path = None
        if body.delete_model:
            if not model_rel:
                raise HTTPException(400, f"Modèle introuvable pour « {name} ».")
            try:
                model_path = files.safe_path(a.settings, model_rel)
                files.check_yaml(a.settings, schema_path)
            except files.FileError as exc:
                raise HTTPException(400, f"Suppression impossible : {exc}") from None

        try:
            recipe_path = rcp.recipe_file(a.settings.project_dir, name)
        except rcp.RecipeError as exc:
            raise HTTPException(400, f"Suppression impossible : {exc}") from None

        touches = [recipe_path]
        if model_path is not None:
            touches.append(model_path)
            try:
                touches.append(files.safe_path(a.settings, schema_path))
            except files.FileError:
                pass  # pas de doc à rendre si son chemin ne résout pas

        erases: list[str] = []
        try:
            with files.all_or_nothing(touches):
                if spec is not None:
                    rcp.delete_recipe(a.settings.project_dir, name)
                    erases.append(f"{rcp.RECIPE_DIR}/{name}.yml")

                if model_path is not None:
                    if model_path.exists():
                        model_path.unlink()
                        erases.append(model_rel)
                    # La doc d'un modèle qui n'existe plus ne documente rien :
                    # dbt s'en plaindrait à chaque parse.
                    if files.remove_doc(a.settings, schema_path, name):
                        erases.append(schema_path)
        except (files.FileError, rcp.RecipeError, OSError) as exc:
            raise HTTPException(400, f"Suppression impossible : {exc}") from None

        parse_error = None
        try:
            a.svc.parse(force=True)
            parse_error = a.svc.manifest_error
        except Exception as exc:  # noqa: BLE001
            parse_error = str(exc)

        a.hub.publish({"type": "flow_changed"})
        return {"deleted": erases, "parse_error": parse_error}

    @app.post("/api/recipe/rename")
    @a.serialized
    def recipe_rename(body: RenameRecipeBody) -> dict:
        """Renomme un modèle, et fait suivre tout ce qui le nommait.

        Un modèle dbt porte son nom à cinq endroits au moins : le fichier
        `.sql`, le script visuel qui l'écrit, l'entrée de `schema.yml` qui le
        documente et le teste, les `ref()` de ce qui le lit, et les entrées des
        scripts visuels d'aval. Renommer le premier seul laisse un projet qui
        parse encore mais ne se construit plus — et la sauvegarde suivante
        depuis l'atelier recrée l'ancien fichier, parce qu'elle retrouve le
        modèle par le nom que porte sa recipe.

        Ce que la route ne fait pas : toucher à l'entrepôt. La table déjà
        construite garde l'ancien nom, `dbt build` en crée une nouvelle à côté,
        et l'ancienne reste jusqu'à ce qu'on la retire. On la nomme dans la
        réponse plutôt que de la supprimer : Pliq ne parle à l'entrepôt qu'en
        lecture, et par dbt.
        """
        try:
            old = rcp.validate_name(body.name)
            new = rcp.validate_name(body.new_name)
        except rcp.RecipeError as exc:
            raise HTTPException(400, str(exc)) from None

        if old == new:
            raise HTTPException(400, f"« {new} » est déjà le nom de ce modèle.")

        _check_identity(a, old, body.model_uid)

        # Un `dbt build` en cours lit les fichiers qu'on s'apprête à déplacer.
        a.reject_during_run("ce renommage")

        spec = rcp.load_recipe(a.settings.project_dir, old)
        _versioned_model(a, old, spec, "le renommage")
        model_rel = _model_file(a, old, spec)
        if model_rel is None:
            raise HTTPException(404, _nothing_to_rename(a, old))
        try:
            model_file = files.safe_path(a.settings, model_rel)
        except files.FileError as exc:
            raise HTTPException(400, str(exc)) from None
        if not model_file.exists():
            raise HTTPException(404, _nothing_to_rename(a, old))

        held = _name_taken(a, new)
        if held:
            raise HTTPException(409, held)

        schema_path = _recipe_schema_path(a, old, spec)
        node = _model_node(a, old)
        old_relation = getattr(node, "relation_name", None) if node else None
        # L'extension du fichier d'origine, pas `.sql` : dbt lit le langage d'un
        # modèle dans son extension, et la forcer transformait un modèle Python
        # en fichier SQL. Le renommage répondait succès — il n'y avait rien à
        # analyser — et c'est le build suivant qui butait sur `def model(...)`.
        new_rel = str(
            Path(model_rel).with_name(f"{new}{Path(model_rel).suffix or '.sql'}")
        )

        # Les scripts visuels d'aval sont réécrits dans leur structure, pas dans
        # leur texte : c'est le champ `ref` d'une entrée qui désigne le modèle.
        scripts: list[tuple[str, dict]] = []
        for name, other in rcp.list_recipes(a.settings.project_dir).items():
            if name != old and rcp.rename_ref_in_spec(
                other, old, new, a.settings.project_name
            ):
                scripts.append((name, other))

        # Le reste du projet — SQL écrit à la main, tests singuliers, cibles de
        # tests de relation dans les YAML — est repris à l'identique, au `ref()`
        # près. On liste avant d'écrire : `all_or_nothing` a besoin de savoir ce
        # qu'il aura à rendre.
        try:
            code = files.code_files_naming(a.settings, old)
        except files.FileError as exc:
            # Un fichier du projet qui mène ailleurs, ou un dossier de code
            # configuré hors de l'arbre : le renommage n'a pas commencé, et
            # c'est bien tout l'intérêt de lister avant d'écrire.
            raise HTTPException(400, f"Renommage impossible : {exc}") from None

        try:
            new_file = files.safe_path(a.settings, new_rel)
        except files.FileError as exc:
            raise HTTPException(400, str(exc)) from None
        # Le manifeste ne voit pas tout : quand le projet ne parse plus, il est
        # vide ou périmé, et `_name_taken` laisse alors passer un fichier qui est
        # pourtant là. Le déplacement l'écraserait sans rien dire.
        if new_file.exists():
            raise HTTPException(
                409,
                f"« {new_rel} » existe déjà. Ouvrez-le pour voir ce qu'il "
                f"contient, ou choisissez un autre nom.",
            )

        old_script = rcp.recipe_file(a.settings.project_dir, old)
        new_script = rcp.recipe_file(a.settings.project_dir, new)
        touches = [model_file, new_file, old_script, new_script]
        touches.append(files.safe_path(a.settings, schema_path))
        touches += code
        touches += [
            rcp.recipe_file(a.settings.project_dir, name) for name, _ in scripts
        ]

        preview = {
            "from": old,
            "to": new,
            "model_path": new_rel,
            # La doc et les tests ne sont pas dans `updates` : ils ne suivent
            # pas un `ref()`, c'est la clé `name` de leur entrée qui change.
            # L'aperçu doit quand même le dire, sinon il sous-compte.
            "schema_path": schema_path,
            "doc_follows": bool(files.read_doc(a.settings, schema_path, old)),
            "recipe_path": f"{rcp.RECIPE_DIR}/{new}.yml" if spec else None,
            "updates": sorted(
                {str(f.relative_to(a.settings.project_dir)) for f in code}
                | {f"{rcp.RECIPE_DIR}/{name}.yml" for name, _ in scripts}
            ),
            "old_relation": old_relation,
            # Ce que le renommage ne peut pas décider à la place de
            # l'utilisateur : une configuration du `dbt_project.yml` rangée
            # sous l'ancien nom. La déplacer à l'aveugle serait un pari ; la
            # taire ferait perdre au modèle sa matérialisation ou ses tags.
            "manual": files.configs_naming(a.settings, old),
        }
        if body.dry_run:
            return {"renamed": False, "dry_run": True, **preview}

        try:
            with files.all_or_nothing(touches):
                # Le SQL compilé ne porte pas le nom du modèle : dbt le tient du
                # nom de fichier. Déplacer le fichier suffit, et le contenu
                # écrit à la main est rendu intact.
                new_file.parent.mkdir(parents=True, exist_ok=True)
                model_file.rename(new_file)

                if spec is not None:
                    spec["name"] = new
                    rcp.save_recipe(a.settings.project_dir, spec)
                    old_script.unlink(missing_ok=True)
                    # La seule ligne du SQL généré qui porte le nom : l'en-tête
                    # qui renvoie au script visuel. Sans ça, elle désigne un
                    # fichier qu'on vient de faire disparaître.
                    new_file.write_text(
                        rcp.rename_banner(new_file.read_text(), old, new)
                    )

                files.rename_doc(a.settings, schema_path, old, new)

                files.rename_refs(a.settings, old, new, code)
                for _, other in scripts:
                    rcp.save_recipe(a.settings.project_dir, other)
        except (files.FileError, rcp.RecipeError, OSError) as exc:
            raise HTTPException(400, f"Renommage impossible : {exc}") from None

        parse_error = None
        try:
            a.svc.parse(force=True)
            parse_error = a.svc.manifest_error
        except Exception as exc:  # noqa: BLE001
            parse_error = str(exc)

        uid = None
        if parse_error is None:
            renamed_node = _model_node(a, new)
            uid = getattr(renamed_node, "unique_id", None) if renamed_node else None

        a.hub.publish({"type": "flow_changed"})
        return {
            "renamed": True,
            "unique_id": uid,
            "parse_error": parse_error,
            **preview,
        }

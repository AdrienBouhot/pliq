"""Le Flow et la fiche d'un dataset : lire, éditer, documenter."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .. import files, graph
from .. import recipes as rcp
from ..dbt_service import RunInProgress
from ..warehouse import WarehouseError
from .. import profiling
from .workshop import Workshop
from .bodies import DocBody, SampleBody, SqlBody, TestsBody
from .reads import node_states, recipe_specs
from .nodes import (
    _columns_of,
    _doc_target,
    _documentable,
    _versioned_card,
    _resolved_freshness,
    _version_of,
    of_project,
    get_node,
    writable_node,
)
from .versions import file_fingerprint, reject_if_stale


def mount(app: FastAPI, a: Workshop) -> None:
    # ----------------------------------------------------------------- infos

    @app.get("/api/project")
    def project_info() -> dict:
        db = a.settings.database_path()
        return {
            "name": a.settings.project_name,
            "project_dir": str(a.settings.project_dir),
            "profile": a.settings.profile_name,
            "target": a.settings.active_target(),
            "targets": a.settings.targets(),
            "adapter": a.settings.target_config().get("type"),
            "database": str(db) if db else None,
            "database_exists": bool(db and db.exists()),
            "manifest_error": a.svc.manifest_error,
            # Un projet existant qui déclare des paquets ne parse pas tant que
            # `dbt deps` n'a pas tourné : autant le dire, et le proposer.
            "deps_needed": a.settings.packages_needed(),
            "run": a.svc.run.summary(),
        }

    @app.get("/api/flow")
    @a.serialized
    def get_flow(refresh: bool = False, fold: str = "") -> dict:
        """`fold` liste les zones repliées, séparées par des virgules.

        Le pliage change la disposition elle-même — colonnes, profondeurs,
        contournements — donc il est appliqué ici, là où le graphe est construit,
        et non masqué après coup dans le navigateur.
        """
        try:
            m = a.svc.parse(force=refresh)
        except RunInProgress:
            # Ce n'est pas une panne du parse : c'est un build qui tient dbt.
            # Sans cette branche, le `except Exception` ci-dessous en ferait un
            # 500, et l'écran afficherait « dbt parse a échoué ». On la laisse
            # repartir telle quelle jusqu'à son propre gestionnaire, qui lui
            # donne son code — sinon ce 409-ci serait le seul à ne pas porter
            # le `run_in_progress` que l'écran reconnaît.
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"dbt parse a échoué : {exc}") from None
        g = graph.collect(
            m,
            recipe_specs(a),
            folded=[z for z in fold.split(",") if z.strip()],
            project=a.settings.project_name,
        )
        g["states"] = node_states(a)
        g["freshness"] = dict(a.svc.freshness)
        g["manifest_error"] = a.svc.manifest_error
        # Les scripts visuels illisibles, nommés un par un : le Flow reste
        # consultable, et l'écran peut dire lequel aller réparer au lieu de
        # présenter ces modèles comme dépourvus de recipe.
        g["recipe_errors"] = rcp.recipes_and_errors(a.settings.project_dir)[1]
        return g

    @app.get("/api/dataset/{uid}")
    @a.serialized
    def dataset_detail(uid: str) -> dict:
        node = get_node(a, uid)
        kind = graph.kind_of(node)
        rel_path = getattr(node, "original_file_path", "") or ""

        raw_sql = None
        sql_digest = None
        schema_path = None
        internal = of_project(a, node)
        if kind == "model" and internal:
            try:
                raw_sql = files.read_text(a.settings, rel_path)
                # Lue en même temps que le contenu : c'est cette version-là que
                # l'éditeur devra rendre pour prouver qu'il a lu la bonne.
                sql_digest = file_fingerprint(a, rel_path)
            except files.FileError:
                raw_sql = getattr(node, "raw_code", None)
            schema_path = files.schema_file_for(rel_path)
        elif kind == "model":
            # Le fichier d'un paquet n'est pas dans ce projet : `rel_path` y
            # désignerait un autre modèle. Le manifest, lui, en garde le texte.
            raw_sql = getattr(node, "raw_code", None)

        # `graph.node_card` et non `graph.collect` : cette route n'a besoin que de
        # ce que dit le manifeste sur *ce* nœud. Construire le Flow entier —
        # disposition et routage compris — pour en extraire une ligne faisait
        # payer à chaque clic sur un dataset le prix du graphe complet.
        meta = graph.node_card(a.svc.manifest(), uid)
        st = a.svc.node_state.get(uid)

        return {
            "id": uid,
            "name": meta.get("name", getattr(node, "name", uid)),
            "kind": kind,
            "layer": meta.get("layer"),
            "materialized": meta.get("materialized"),
            "description": (getattr(node, "description", "") or "").strip(),
            "path": rel_path,
            "schema_path": schema_path,
            # Ce que l'écran a le droit de proposer : un modèle de paquet
            # s'ouvre en lecture, et le dit.
            "package": str(getattr(node, "package_name", "") or ""),
            "editable": internal and kind == "model",
            "schema": getattr(node, "schema", ""),
            "database": getattr(node, "database", ""),
            "relation": getattr(node, "relation_name", None),
            "tags": list(getattr(node, "tags", []) or []),
            "documented_columns": [
                {
                    "name": n,
                    "description": (getattr(c, "description", "") or "").strip(),
                }
                for n, c in (getattr(node, "columns", {}) or {}).items()
            ],
            "tests": meta.get("tests", []),
            "raw_sql": raw_sql,
            "sql_digest": sql_digest,
            "compiled_sql": a.svc.compiled_sql(node) if kind == "model" else None,
            "state": {
                "status": st.status if st else "idle",
                "execution_time": st.execution_time if st else None,
                "rows_affected": st.rows_affected if st else None,
            },
        }

    @app.post("/api/dataset/{uid}/explore")
    @a.serialized
    def explore(uid: str, body: SampleBody) -> dict:
        node = get_node(a, uid)
        relation = getattr(node, "relation_name", None)
        try:
            data = a.wh.preview_relation(relation, body.limit)
        except WarehouseError as exc:
            raise HTTPException(400, str(exc)) from None
        cols = profiling.profile(data["columns"], data["rows"])
        return {
            "columns": cols,
            "rows": data["rows"],
            "relation": relation,
            "total": a.wh.count(relation),
        }

    @app.put("/api/dataset/{uid}/sql")
    @a.serialized
    def save_sql(uid: str, body: SqlBody) -> dict:
        a.reject_during_run("cet enregistrement")
        node = writable_node(a, uid)
        if graph.kind_of(node) != "model":
            raise HTTPException(400, "Seuls les modèles sont éditables.")
        rel_path = getattr(node, "original_file_path", "")
        # Un modèle produit par une recipe ne s'écrit pas ici : le prochain
        # enregistrement du script visuel régénère son fichier, et ce qu'on
        # aurait tapé partirait avec. L'écran le sait déjà — il n'offre alors
        # que « Voir le SQL » — mais la route, elle, l'acceptait : l'atelier
        # fabriquait lui-même la divergence dont l'enregistrement se plaint.
        if rcp.load_recipe(a.settings.project_dir, node.name) is not None:
            raise HTTPException(
                409,
                f"« {node.name} » est écrit par un script visuel : son SQL est "
                f"regénéré à chaque enregistrement de la recipe, et cette "
                f"modification serait perdue. Ouvrez la recipe pour le changer, "
                f"ou supprimez-la en gardant le SQL pour reprendre ce modèle à "
                f"la main.",
            )
        reject_if_stale(a, rel_path, body, file_fingerprint(a, rel_path))
        try:
            files.write_text(a.settings, rel_path, body.sql)
        except files.FileError as exc:
            raise HTTPException(400, str(exc)) from None
        # L'empreinte de ce qu'on vient d'écrire : l'éditeur resté ouvert la
        # garde, et la sauvegarde suivante ne se croit pas en conflit avec
        # celle-ci. Même service que `base` pour les recipes.
        digest = file_fingerprint(a, rel_path)
        try:
            a.svc.parse(force=True)
        except Exception as exc:  # noqa: BLE001
            return {
                "saved": True,
                "path": rel_path,
                "digest": digest,
                "parse_error": str(exc),
            }
        a.hub.publish({"type": "flow_changed"})
        return {
            "saved": True,
            "path": rel_path,
            "digest": digest,
            "parse_error": a.svc.manifest_error,
        }

    @app.post("/api/dataset/{uid}/doc")
    @a.serialized
    def save_doc(uid: str, body: DocBody) -> dict:
        a.reject_during_run("cet enregistrement")
        node = writable_node(a, uid)
        if graph.kind_of(node) != "model":
            raise HTTPException(400, "Seuls les modèles ont une fiche éditable.")
        _versioned_card(a, node, "l'enregistrement")
        # `_doc_target` et non le schema.yml voisin : documenter deux fois le
        # même modèle, dans deux fichiers, casse le parse de dbt.
        schema_path = _doc_target(a, node)[0]
        reject_if_stale(
            a,
            f"La documentation de {node.name}",
            body,
            files.doc_digest(a.settings, schema_path, node.name),
        )
        try:
            files.validate_columns(body.columns)
            files.upsert_doc(
                a.settings,
                schema_path,
                node.name,
                kind="model",
                description=body.description,
                columns=body.columns,
            )
        except files.FileError as exc:
            raise HTTPException(400, str(exc)) from None
        try:
            a.svc.parse(force=True)
        except Exception as exc:  # noqa: BLE001
            return {"saved": True, "path": schema_path, "parse_error": str(exc)}
        a.hub.publish({"type": "flow_changed"})
        return {"saved": True, "path": schema_path, "parse_error": a.svc.manifest_error}

    @app.get("/api/dataset/{uid}/tests")
    @a.serialized
    def get_tests(uid: str) -> dict:
        node = _documentable(a, uid)
        version = _version_of(a, node)
        internal = of_project(a, node)
        # Deux fiches que l'atelier ne sait pas écrire, et pour deux raisons
        # différentes : un modèle versionné, dont le YAML porte la propriété à
        # un autre niveau, et un nœud de paquet, dont le YAML n'est pas dans ce
        # projet. Dans les deux cas la lecture ne doit pas venir du disque.
        #
        # Le `patch_path` d'un nœud de paquet est relatif à la racine de *son*
        # paquet — `vendor://models/schema.yml` — et le résoudre depuis le
        # projet ouvert ouvrait le `schema.yml` local : la fiche du paquet
        # annonçait la description, les tags, les colonnes et les tests du
        # modèle local, et nommait un fichier de ce projet comme étant le sien.
        # La route voisine `GET /api/dataset/{uid}`, qui lit le nœud, disait
        # l'inverse sur le même dataset.
        of_node = bool(version) or not internal

        if of_node:
            schema_path = None
            kind = graph.kind_of(node)
            source_name = str(getattr(node, "source_name", "") or "")
            entry: dict = {}
            doc_digest = None
        else:
            schema_path, kind, source_name = _doc_target(a, node)
            entry = files.read_doc(
                a.settings, schema_path, node.name, kind=kind, source_name=source_name
            )
            # Ce que la sauvegarde exigera pour prouver qu'elle a lu cette
            # version-là de la fiche, et pas celle d'avant.
            doc_digest = files.doc_digest(
                a.settings, schema_path, node.name, kind=kind, source_name=source_name
            )

        documented = {c.get("name"): c for c in (entry.get("columns") or [])}
        # dbt a déjà résolu ce qui s'applique à ce nœud-là : l'héritage d'une
        # version sur la propriété commune, comme la documentation que le
        # paquet porte pour lui-même. On prend son résultat.
        resolved = {
            col_name: (getattr(col, "description", "") or "")
            for col_name, col in (getattr(node, "columns", {}) or {}).items()
        }

        def _description_col(col_name: str, doc: dict) -> str:
            if of_node:
                return str(resolved.get(col_name, "") or "")
            return str(doc.get("description") or "")

        def _tests_col(doc: dict) -> list:
            # Les tests se lisent dans le YAML, et il n'y en a pas à lire ici.
            # Mieux vaut n'en montrer aucun que ceux d'un modèle voisin : le
            # panneau du dataset, lui, les tient du manifeste.
            return [] if of_node else files.parse_column_tests(doc)

        columns = []
        for col in _columns_of(a, node):
            doc = documented.get(col["name"], {})
            columns.append(
                {
                    "name": col["name"],
                    "type": col.get("type"),
                    "description": _description_col(col["name"], doc),
                    "tests": _tests_col(doc),
                }
            )
        # Colonnes documentées mais absentes de l'entrepôt : on les garde visibles.
        # Pour une fiche lue sur le nœud, ce sont celles que dbt lui connaît.
        known = {c["name"] for c in columns}
        remaining = resolved if of_node else documented
        for name in remaining:
            if name not in known:
                doc = documented.get(name, {})
                columns.append(
                    {
                        "name": name,
                        "type": None,
                        "missing": True,
                        "description": _description_col(name, doc),
                        "tests": _tests_col(doc),
                    }
                )

        return {
            "model": node.name,
            "kind": kind,
            "version": version,
            "source_name": source_name,
            "schema_path": schema_path,
            # Ce que l'écran a le droit de proposer. Un modèle de paquet ne
            # s'écrit pas d'ici, et une fiche versionnée s'écrirait à côté :
            # dans les deux cas la sauvegarde refuse, et l'écran doit le dire
            # avant qu'on remplisse le formulaire, pas après.
            "editable": internal and not version,
            "readonly_reason": (
                f"Modèle versionné : cette fiche est celle de la v{version}, "
                f"mais l'atelier n'écrit que les propriétés communes à toutes "
                f"les versions. Passez par le YAML du projet."
                if version
                else (
                    None
                    if internal
                    else f"Installé par le paquet "
                    f"« {getattr(node, 'package_name', '')} » : sa fiche vit "
                    f"dans le dépôt du paquet, et se lit ici telle que dbt l'a "
                    f"résolue."
                )
            ),
            "doc_digest": doc_digest,
            "description": (
                str(getattr(node, "description", "") or "").strip()
                if of_node
                else str(entry.get("description") or "")
            ),
            "tags": [
                str(t)
                for t in (
                    getattr(node, "tags", []) if of_node else (entry.get("tags") or [])
                )
            ],
            "columns": columns,
            "relation": getattr(node, "relation_name", None),
            # Ce qui décide si `dbt source freshness` regarde cette table, et
            # à partir de quand il s'inquiète.
            "freshness": _resolved_freshness(a, node) if kind == "source" else None,
            "freshness_periods": list(files.FRESHNESS_PERIODS),
            "freshness_state": a.svc.freshness.get(uid),
        }

    @app.post("/api/dataset/{uid}/tests")
    @a.serialized
    def save_tests(uid: str, body: TestsBody) -> dict:
        a.reject_during_run("cet enregistrement")
        node = _documentable(a, uid, to_write=True)
        _versioned_card(a, node, "l'enregistrement")
        schema_path, kind, source_name = _doc_target(a, node)
        reject_if_stale(
            a,
            f"La fiche de {node.name}",
            body,
            files.doc_digest(
                a.settings, schema_path, node.name, kind=kind, source_name=source_name
            ),
        )
        try:
            files.upsert_doc(
                a.settings,
                schema_path,
                node.name,
                kind=kind,
                source_name=source_name,
                description=body.description,
                columns=body.columns,
                tags=body.tags,
                freshness=body.freshness if kind == "source" else None,
                resolved_freshness=(
                    _resolved_freshness(a, node) if kind == "source" else None
                ),
            )
        except files.FileError as exc:
            raise HTTPException(400, str(exc)) from None

        parse_error = None
        try:
            a.svc.parse(force=True)
            parse_error = a.svc.manifest_error
        except Exception as exc:  # noqa: BLE001
            parse_error = str(exc)
        a.hub.publish({"type": "flow_changed"})
        return {
            "saved": True,
            "path": schema_path,
            "parse_error": parse_error,
            # L'empreinte de ce qu'on vient d'écrire : sans elle, un second
            # enregistrement depuis le même formulaire se heurtait à l'empreinte
            # de la version d'avant, et se croyait en conflit avec lui-même.
            "base": files.doc_digest(
                a.settings, schema_path, node.name, kind=kind, source_name=source_name
            ),
        }

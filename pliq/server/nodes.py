"""Retrouver un nœud dbt, et dire ce qu'on a le droit d'en faire.

Un nom ne désigne pas un modèle : deux paquets peuvent déclarer `orders`,
et un modèle versionné porte le même nom à toutes ses versions. Presque
tout ce qui suit existe pour que l'atelier n'écrive jamais dans le
fichier d'un homonyme.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .. import files, graph
from .. import recipes as rcp
from ..warehouse import WarehouseError
from .workshop import Workshop

# --------------------------------------------------------------- helpers


def nodes_by_id(a: Workshop) -> dict[str, Any]:
    m = a.svc.manifest()
    out: dict[str, Any] = dict(m.nodes)
    out.update(m.sources)
    return out


def get_node(a: Workshop, uid: str):
    node = nodes_by_id(a).get(uid)
    if node is None:
        raise HTTPException(404, f"Dataset inconnu : {uid}")
    return node


def of_project(a: Workshop, node) -> bool:
    """Le projet ouvert possède-t-il ce nœud ? (cf. `graph.belongs_to_project`)"""
    return graph.belongs_to_project(node, a.settings.project_name)


def writable_node(a: Workshop, uid: str):
    """Le nœud, à condition que le projet ouvert le possède.

    Toutes les écritures passent par ici. Un nœud de paquet porte un chemin
    relatif à *son* paquet : résolu depuis le projet ouvert, il désigne au
    mieux rien, au pire le modèle local du même nom — qu'on écraserait en
    croyant modifier le paquet.
    """
    node = get_node(a, uid)
    if of_project(a, node):
        return node
    pkg = str(getattr(node, "package_name", "") or "")
    raise HTTPException(
        403,
        f"« {getattr(node, 'name', uid)} » vient du paquet « {pkg} », "
        f"installé par dbt deps : il ne s'édite pas depuis l'atelier. "
        f"Modifiez-le dans le dépôt du paquet, ou créez un modèle à vous "
        f"qui le référence.",
    )


def _input_node(a: Workshop, inp: dict):
    """Le nœud de manifeste d'une entrée de recipe, ref ou source."""
    try:
        m = a.svc.manifest()
    except Exception:  # noqa: BLE001
        return None
    return graph.input_node_id(m, inp, a.settings.project_name)


def _doc_target(a: Workshop, node) -> tuple[str, str, str]:
    """Où documenter ce nœud : (chemin du YAML, type, groupe de sources).

    dbt note lui-même dans `patch_path` le fichier qui documente une
    ressource : on écrit là où c'est déjà écrit, sinon à la convention.
    """
    kind = graph.kind_of(node)
    if kind == "source":
        return (
            getattr(node, "original_file_path", ""),
            "source",
            str(getattr(node, "source_name", "")),
        )

    patch = getattr(node, "patch_path", None)
    if patch:
        return str(patch).split("://", 1)[-1], kind, ""
    if kind == "seed":
        seed_dirs = a.settings.project_yml.get("seed-paths") or ["seeds"]
        return f"{seed_dirs[0]}/schema.yml", kind, ""
    return files.schema_file_for(getattr(node, "original_file_path", "")), kind, ""


# Les trois familles que dbt sait documenter, plus les sources.
DOCUMENTABLE = ("model", "seed", "snapshot", "source")


def _documentable(a: Workshop, uid: str, *, to_write: bool = False):
    node = writable_node(a, uid) if to_write else get_node(a, uid)
    kind = graph.kind_of(node)
    if kind not in DOCUMENTABLE:
        raise HTTPException(400, f"Un nœud de type « {kind} » ne se documente pas.")
    return node


def _version_of(a: Workshop, node) -> str:
    """La version que porte ce nœud, ou "" s'il n'en a pas."""
    v = getattr(node, "version", None)
    return "" if v is None else str(v)


def _versioned_card(a: Workshop, node, what: str) -> None:
    """Refuse d'écrire la fiche d'un modèle versionné.

    L'URL désigne bien `model.demo.orders.v1`, mais tout ce qui écrit la
    documentation ne reçoit que `node.name` : `orders`. Ça vise l'entrée
    commune `models[name=orders]`, jamais `versions[v=1]`. Enregistrer la
    fiche de la v1 répondait donc 200 en écrivant une propriété commune —
    que la description propre de la v1 continuait d'ailleurs à masquer — et
    la fiche de la v2 se mettait à afficher ce qu'on avait écrit pour la v1.
    L'écran annonçait une modification qui ne correspondait à rien.

    Tant que la lecture et l'écriture ne portent pas la version, mieux vaut
    refuser que d'écrire à côté : c'est déjà ce que font `_versioned_model`
    pour le renommage et la suppression. La lecture, elle, reste ouverte —
    elle rend ce que dbt a résolu pour cette version-là.
    """
    version = _version_of(a, node)
    if not version:
        return
    raise HTTPException(
        409,
        f"« {node.name} » est un modèle versionné, et cet écran regarde sa "
        f"v{version} : {what} écrirait la propriété commune à toutes les "
        f"versions, sans que la v{version} en change pour autant. Passez "
        f"par le YAML du projet, sous « versions: - v: {version} ».",
    )


def _columns_of(a: Workshop, node) -> list[dict]:
    """Colonnes réelles si l'entrepôt les connaît, sinon celles du YAML."""
    relation = getattr(node, "relation_name", None)
    if relation:
        try:
            return [
                {"name": c["name"], "type": c["type"]}
                for c in a.wh.columns_of_sql(f"select * from {relation}")
            ]
        except WarehouseError:
            pass
    return [
        {"name": n, "type": getattr(c, "data_type", None)}
        for n, c in (getattr(node, "columns", {}) or {}).items()
    ]


def _resolved_freshness(a: Workshop, node) -> dict:
    """Ce que `dbt source freshness` appliquera vraiment à cette table.

    Et non ce que son entrée YAML porte : dbt résout d'abord ce que la
    table hérite de son groupe `sources:`, ce que le bloc `config:` déclare
    — où ces deux propriétés vivent depuis dbt 1.10 — et ce que
    `dbt_project.yml` dit des sources. Lire la seule entrée de la table
    montrait un formulaire vide là où un seuil d'une heure était actif ;
    l'y remplacer par 48 heures répondait « enregistré » et ne changeait
    rien, et le vider ne désactivait pas davantage.
    """
    thresholds = getattr(node, "freshness", None)
    out: dict = {"loaded_at_field": str(getattr(node, "loaded_at_field", "") or "")}
    for key in ("warn_after", "error_after"):
        t = getattr(thresholds, key, None)
        count = getattr(t, "count", None)
        period = getattr(t, "period", None)
        if not count or period is None:
            continue
        # `period` est une énumération dbt : le YAML porte sa valeur, pas
        # le nom de son membre Python.
        out[key] = {
            "count": int(count),
            "period": str(getattr(period, "value", period)),
        }
    return out


def _downstream_models(a: Workshop, name: str) -> list:
    """Les modèles qui lisent directement celui-ci.

    Directement, et pas de proche en proche : une colonne renommée casse
    ceux qui la nomment, et eux seuls. Ce qui vient après ne casse que si
    le voisin la laissait passer — ce qu'on ne peut pas savoir d'ici sans
    mentir.
    """
    node = _model_node(a, name)
    if node is None:
        return []
    uid = getattr(node, "unique_id", None)
    out = []
    try:
        nodes = a.svc.manifest().nodes.values()
    except Exception:  # noqa: BLE001
        return []
    for other in nodes:
        if graph.kind_of(other) not in ("model", "snapshot"):
            continue
        depend = getattr(getattr(other, "depends_on", None), "nodes", []) or []
        if uid in depend:
            out.append(other)
    return out


def _mentions(a: Workshop, node, column: str) -> str:
    """Où ce modèle lit cette colonne : son script visuel, ou son SQL.

    Un modèle qui se contente d'un `select *` ne la nomme nulle part et
    n'est donc pas signalé : il ne casse pas, sa sortie change simplement
    de forme. Ce qu'on cherche, c'est ce qui échouera au `dbt build`.
    """
    name = getattr(node, "name", "") or ""
    spec = (
        rcp.load_recipe(a.settings.project_dir, name) if of_project(a, node) else None
    )
    if spec is not None:
        return "script" if rcp.mentions_column(spec, column) else ""
    raw_text = getattr(node, "raw_code", "") or ""
    return "sql" if rcp.sql_mentions_column(raw_text, column) else ""


def _nothing_to_rename(a: Workshop, name: str) -> str:
    """Pourquoi ce nom ne se renomme pas : introuvable, ou pas un modèle."""
    try:
        nodes = list(a.svc.manifest().nodes.values()) + list(
            a.svc.manifest().sources.values()
        )
    except Exception:  # noqa: BLE001
        nodes = []
    for other in nodes:
        if getattr(other, "name", None) != name:
            continue
        kind = graph.kind_of(other)
        if kind == "seed":
            return (
                f"« {name} » est un seed : son nom est celui de son fichier "
                f"CSV. Renommez le CSV, et les ref() qui le nomment."
            )
        if kind == "source":
            return (
                f"« {name} » est une table de source : son nom est celui "
                f"qu'elle porte dans l'entrepôt, pas un nom de modèle."
            )
        if kind == "snapshot":
            return (
                f"« {name} » est un snapshot : le renommer déplacerait "
                f"l'historique déjà collecté. L'atelier ne le fait pas."
            )
    return f"Aucun modèle « {name} » dans ce projet."


def _name_taken(a: Workshop, name: str) -> str:
    """Ce qui empêche d'appeler un modèle ainsi, ou « » si la place est libre.

    Trois façons pour un nom d'être pris, et dbt ne les signale pas de la
    même manière : deux ressources du même nom cassent le parse, un fichier
    déjà là serait écrasé, et un script visuel orphelin réapparaîtrait au
    prochain enregistrement sous ce nom.

    Le manifeste passe en premier parce qu'il voit tout le projet, mais il
    ne fait foi que là où il correspond encore au disque : un nœud qui
    désigne un fichier supprimé à la main est périmé, et s'en servir dirait
    à l'utilisateur d'aller ouvrir un fichier qui n'existe plus. On le
    laisse alors aux contrôles suivants, qui regardent ce qui est là.
    """
    try:
        nodes = a.svc.manifest().nodes.values()
    except Exception:  # noqa: BLE001
        nodes = []
    for other in nodes:
        if getattr(other, "name", None) != name:
            continue
        kind = graph.kind_of(other)
        if kind not in ("model", "seed", "snapshot"):
            continue
        path = getattr(other, "original_file_path", "")
        if path and not (a.settings.project_dir / path).exists():
            continue
        return (
            f"« {name} » est déjà pris par un {kind} du projet"
            + (f" ({path})" if path else "")
            + " : dbt refuse deux ressources du même nom."
        )
    rel = _homonym_model_file(a, name)
    if rel:
        return (
            f"« {rel} » existe déjà. Ouvrez ce modèle pour le modifier, ou "
            f"choisissez un autre nom : dbt refuse deux ressources du même "
            f"nom, même dans deux couches différentes."
        )
    if rcp.recipe_file(a.settings.project_dir, name).exists():
        return (
            f"Un script visuel « {rcp.RECIPE_DIR}/{name}.yml » existe déjà. "
            f"Ouvrez-le, ou supprimez-le avant de réutiliser ce nom."
        )
    return ""


def _homonym_model_file(a: Workshop, name: str) -> str | None:
    """Un `<nom>.sql` posé quelque part dans les `model-paths`, s'il y en a.

    Le manifeste ne suffit pas à dire qu'un nom est libre : quand le projet
    ne parse plus, il est vide ou périmé, et un fichier bien réel y est
    invisible. Le disque, lui, dit toujours la vérité — et c'est lui que dbt
    lira au prochain parse.
    """
    for base in a.settings.model_paths():
        if not base.is_dir():
            continue
        for f in base.rglob(f"{name}.sql"):
            if f.is_file():
                try:
                    return str(f.relative_to(a.settings.project_dir))
                except ValueError:
                    return str(f)
    return None


def _model_node(a: Workshop, name: str):
    """Le nœud de manifeste du modèle *du projet* qui porte ce nom.

    Du projet, et pas du premier paquet venu : tout ce qui est adressé par
    nom — les recipes — décrit des fichiers de ce projet-ci. Rendre le
    premier homonyme rencontré faisait dépendre la réponse de l'ordre du
    manifest, et une action partie de la recipe d'un modèle de paquet
    résolvait vers le modèle local, qu'elle lisait, réécrivait ou
    supprimait. `writable_node` pose déjà la même règle pour tout ce qui
    est adressé par `unique_id`.
    """
    try:
        for node in a.svc.manifest().nodes.values():
            if getattr(node, "name", None) != name:
                continue
            if graph.kind_of(node) == "model" and of_project(a, node):
                return node
    except Exception:  # noqa: BLE001
        pass
    return None


def _check_identity(a: Workshop, name: str, uid: str | None) -> None:
    """Le nom adressé et l'identité affichée désignent-ils le même modèle ?

    Une recipe s'adresse par nom, et un nom n'est unique qu'à l'intérieur
    d'un paquet : `orders` peut être à la fois un modèle de ce projet et un
    modèle d'un paquet installé par `dbt deps`. L'écran, lui, sait de quel
    nœud part le geste — le Flow lui en donne l'`unique_id`. On le lui
    redemande pour tout ce qui écrit : sans ça, une suppression partie de
    la recipe d'un modèle de paquet effaçait le `.sql` du modèle local
    homonyme, et répondait 200.

    `writable_node` porte le refus : c'est la même règle que pour les
    routes adressées par `unique_id`, et le même message. Un client qui
    n'envoie pas d'identité garde l'ancien comportement, comme pour les
    empreintes de concurrence — la garantie est en plus, pas un passage
    obligé.
    """
    if not uid:
        return
    node = writable_node(a, uid)
    kind = graph.kind_of(node)
    if kind != "model":
        raise HTTPException(
            400, f"« {getattr(node, 'name', uid)} » est un {kind}, pas un modèle."
        )
    if getattr(node, "name", None) != name:
        raise HTTPException(
            409,
            f"L'écran désigne « {getattr(node, 'name', uid)} » et la "
            f"requête « {name} » : rechargez le Flow avant de recommencer.",
        )


def _model_versions(a: Workshop, name: str, spec: dict | None = None) -> list:
    """Les versions déclarées du modèle qui porte ce nom, s'il en a.

    dbt donne le même `name` à toutes les versions d'un modèle : `customers`
    v1 et v2 sont deux nœuds et deux fichiers pour un seul nom. Or l'atelier
    désigne un modèle par son nom — il n'a rien pour dire de quelle version
    il parle, et `_model_node` rend simplement la première rencontrée.

    Le manifeste d'abord, qui porte la version sur chaque nœud. Puis le YAML,
    parce qu'un projet qui ne parse plus laisse le manifeste vide ou périmé,
    et que c'est justement là que le renommage écrirait.
    """
    seen = []
    try:
        for node in a.svc.manifest().nodes.values():
            if getattr(node, "name", None) != name:
                continue
            if graph.kind_of(node) != "model" or not of_project(a, node):
                continue
            v = getattr(node, "version", None)
            if v is not None and v not in seen:
                seen.append(v)
    except Exception:  # noqa: BLE001
        pass
    if seen:
        return seen
    entry = files.read_doc(a.settings, _recipe_schema_path(a, name, spec), name)
    for v in entry.get("versions") or []:
        raw_text = v.get("v") if isinstance(v, dict) else v
        if raw_text is not None and raw_text not in seen:
            seen.append(raw_text)
    return seen


def _versioned_model(a: Workshop, name: str, spec: dict | None, what: str) -> None:
    """Refuse une opération qui ne saurait traiter qu'une version sur deux.

    Ce que ferait le renommage sans ce garde-fou : déplacer le seul fichier
    que `_model_node` a trouvé, renommer l'entrée YAML entière — versions
    comprises — et réécrire les `ref()` de tout l'aval, versions comprises
    elles aussi. Le fichier déplacé perd son suffixe, donc devient la
    *dernière* version du nouveau nom : un consommateur de `v=2` se met à
    lire la logique de la v1. Rien n'échoue — ni le parse, ni le build — et
    la version restée en arrière sort du graphe sans un mot.

    Tant que l'atelier n'a pas d'identité de version — ni `defined_in`, ni
    `latest_version`, ni les fichiers `_v<n>` — il vaut mieux ne pas y
    toucher que d'en traiter la moitié.
    """
    versions = _model_versions(a, name, spec)
    if not versions:
        return
    version_list = ", ".join(f"v{v}" for v in versions)
    raise HTTPException(
        409,
        f"« {name} » est un modèle versionné ({version_list}) : {what} ne "
        f"traiterait qu'un seul de ses fichiers. Les autres versions "
        f"resteraient en arrière, et ce qui lit « {name} » changerait de "
        f"logique sans qu'aucun build n'échoue. Passez par les fichiers du "
        f"projet, en reprenant chaque version et son « defined_in ».",
    )


def _model_file(a: Workshop, name: str, spec: dict | None) -> str | None:
    """Le fichier .sql d'un modèle : ce que dbt en dit, sinon la recipe.

    Le manifeste fait foi — un modèle déplacé à la main n'est plus là où la
    recipe croit l'écrire. Mais il peut manquer (projet qui ne parse plus),
    et le spec suffit alors à retrouver le chemin.
    """
    node = _model_node(a, name)
    rel = getattr(node, "original_file_path", None) if node is not None else None
    if rel:
        return str(rel)
    if spec:
        try:
            return _model_path(a, spec)
        except rcp.RecipeError:
            return None
    return None


def _recipe_schema_path(a: Workshop, name: str, spec: dict | None) -> str:
    """Le YAML dbt qui documente le modèle d'une recipe.

    Le `schema.yml` voisin n'est qu'une convention : dbt note dans
    `patch_path` le fichier qui documente réellement une ressource. Écrire
    ailleurs qu'à cet endroit y créait un doublon, et dbt refusait de parser
    deux documentations pour un même modèle.
    """
    node = _model_node(a, name)
    if node is not None:
        return _doc_target(a, node)[0]
    rel = _model_file(a, name, spec)
    if not rel:
        base = next(iter(a.settings.model_dirs()), "models")
        rel = f"{base}/{name}.sql"
    return files.schema_file_for(rel)


def _layer_of(a: Workshop, spec: dict) -> str:
    return ((spec.get("output") or {}).get("layer") or "").strip()


def _model_path(a: Workshop, spec: dict) -> str:
    # Le chemin entier des `model-paths`, pas seulement leur dernier
    # dossier : avec `model-paths: ["transform/models"]`, écrire dans
    # `models/` produit un fichier que dbt ne découvre jamais.
    return rcp.model_path(spec, next(iter(a.settings.model_dirs()), "models"))

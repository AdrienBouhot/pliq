"""Faire entrer une table dans le Flow.

Dans dbt une table de départ existe d'une seule façon qui nous concerne : la
**source** — elle est déjà dans l'entrepôt, on se contente de la déclarer. Le
reste du Flow, ce sont des **modèles**, produits par les recipes.

Charger des données n'est pas le travail de l'atelier : l'entrepôt est alimenté
par ailleurs (ingestion, ETL amont), dbt transforme ce qui s'y trouve.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ruamel.yaml.scalarstring import DoubleQuotedScalarString as DQ

from . import files, graph
from .config import Settings
from .warehouse import Warehouse

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatasetError(RuntimeError):
    pass


def _check_name(name: str, what: str = "nom") -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise DatasetError(
            f"{what.capitalize()} invalide : « {name} ». "
            f"Lettres, chiffres et « _ » seulement, en commençant par une lettre."
        )
    return name


# ---------------------------------------------------------- ce que dbt connaît


def known_relations(manifest) -> dict[tuple[str, str, str], dict]:
    """Index (base, schéma, table) → nœud dbt, pour savoir ce qui est déjà déclaré.

    Les trois niveaux, et pas seulement les deux derniers : avec deux bases
    attachées, `db_two.main.orders` se voyait marquée « déjà déclarée » parce
    que `memory.main.orders` l'était. On proposait alors de ne pas déclarer une
    table qui ne l'avait jamais été.

    Un nœud qui ne dit pas sa base est indexé sous `""` : la comparaison retombe
    dessus quand l'entrepôt, lui, nomme un catalogue.
    """
    out: dict[tuple[str, str, str], dict] = {}

    def key(node) -> tuple[str, str, str] | None:
        schema = getattr(node, "schema", None)
        ident = (
            getattr(node, "identifier", None)
            or getattr(node, "alias", None)
            or getattr(node, "name", None)
        )
        if not schema or not ident:
            return None
        database = getattr(node, "database", None) or ""
        return (str(database).lower(), str(schema).lower(), str(ident).lower())

    for node in manifest.nodes.values():
        rt = str(getattr(node, "resource_type", ""))
        kind = (
            "seed" if rt.endswith("seed") else "model" if rt.endswith("model") else None
        )
        if kind is None:
            continue
        k = key(node)
        if k:
            out[k] = {"kind": kind, "label": node.name, "id": node.unique_id}

    for src in manifest.sources.values():
        k = key(src)
        if k:
            out[k] = {
                "kind": "source",
                "label": f"{src.source_name}.{src.name}",
                "id": src.unique_id,
            }
    return out


def existing_sources(settings: Settings, manifest) -> list[dict]:
    """Les groupes de sources déjà déclarés, pour proposer d'y ajouter une table."""
    groups: dict[str, dict] = {}
    for src in manifest.sources.values():
        g = groups.setdefault(
            str(src.source_name),
            {
                "name": str(src.source_name),
                "schema": getattr(src, "schema", "") or "",
                "database": getattr(src, "database", "") or "",
                "tables": [],
                "path": getattr(src, "original_file_path", "") or "",
            },
        )
        g["tables"].append(src.name)
    for g in groups.values():
        g["tables"].sort()
    return sorted(groups.values(), key=lambda g: g["name"])


def inventory(settings: Settings, wh: Warehouse, manifest) -> dict:
    """Tables de l'entrepôt, marquées « déjà déclarée » ou non."""
    data = wh.tables()
    known = known_relations(manifest)
    for t in data["tables"]:
        schema, name = t["schema"].lower(), t["name"].lower()
        # Le repli sur `""` vaut pour un nœud dont le manifeste ne dit pas la
        # base ; il ne confond pas deux catalogues, puisqu'un nœud qui nomme la
        # sienne n'est indexé que sous celle-là.
        hit = known.get((t["database"].lower(), schema, name)) or known.get(
            ("", schema, name)
        )
        t["declared"] = hit
    return {
        "database": data["database"],
        "tables": data["tables"],
        # L'écran doit pouvoir dire « et d'autres » plutôt que de présenter
        # une liste tronquée comme complète.
        "truncated": bool(data.get("truncated")),
        "limit": data.get("limit"),
        "sources": existing_sources(settings, manifest),
    }


# --------------------------------------------------------------------- sources


def _sources_file(settings: Settings, source_name: str) -> tuple[str, dict]:
    """Où écrire ce groupe de sources : le fichier qui le déclare déjà, sinon
    la convention `models/<staging>/sources.yml`."""
    for rel, data in files.find_yaml_with(settings, "sources", settings.model_paths()):
        for entry in data.get("sources") or []:
            if isinstance(entry, dict) and entry.get("name") == source_name:
                return rel, data

    # Pas encore de groupe portant ce nom : un fichier sources.yml existant fait
    # l'affaire, sinon on en crée un à la racine des modèles.
    candidates = files.find_yaml_with(settings, "sources", settings.model_paths())
    if candidates:
        return candidates[0]

    base = next((p for p in settings.model_paths()), settings.project_dir / "models")
    rel_base = base.relative_to(settings.project_dir)
    staging = base / "staging"
    if staging.is_dir():
        return str(rel_base / "staging" / "sources.yml"), {}
    return str(rel_base / "sources.yml"), {}


def group_database(manifest, source_name: str) -> str:
    """La base que dbt donne aux tables de ce groupe, héritage compris.

    Le YAML ne suffit pas à la dire : un groupe sans `database:` n'est pas un
    groupe sans base, c'est un groupe sur celle de la cible. Le manifeste, lui,
    porte la base *résolue* — celle que dbt lira vraiment — et c'est la seule
    à laquelle il vaille la peine de comparer.

    Vide quand le groupe n'a encore aucune table : dbt n'en sait alors rien non
    plus, et on ne peut que s'en tenir au YAML.
    """
    if manifest is None:
        return ""
    for src in manifest.sources.values():
        if str(getattr(src, "source_name", "")) == source_name:
            return str(getattr(src, "database", "") or "")
    return ""


def _check_group_matches(
    group: dict,
    source_name: str,
    schema: str,
    database: str | None,
    effective_database: str = "",
) -> None:
    """Refuse d'ajouter une table à un groupe qui ne pointe pas sur son schéma.

    Un groupe de sources porte *un* schéma, et toutes ses tables en héritent :
    ajouter `schema_b.customers` au groupe `raw`, déjà rattaché à `schema_a`,
    déclarait en fait `schema_a.customers`. Au mieux dbt ne trouve pas la
    table, au pire il en lit une autre qui porte le même nom — sans que rien
    ne le dise.

    Un groupe sans `schema:` explicite n'est pas un groupe sans schéma : dbt
    prend alors le nom du groupe. C'est donc à cela qu'on compare.
    """
    # La comparaison ignore la casse, pour la raison qui vaut déjà pour la base
    # juste en dessous : `inventory()` rend le schéma dans la casse de
    # l'entrepôt — `MAIN` sur Snowflake, qui replie ses identifiants en
    # majuscules — là où le YAML du groupe porte `main`. Refuser là-dessus
    # bloquait une déclaration que dbt aurait lue sans broncher. Le message,
    # lui, garde les deux casses d'origine : c'est ce qui est écrit de part et
    # d'autre, et le lecteur doit pouvoir s'y retrouver.
    current = str(group.get("schema") or source_name).strip()
    if current.casefold() != schema.casefold():
        raise DatasetError(
            f"Le groupe de sources « {source_name} » lit le schéma "
            f"« {current} », pas « {schema} » : ses tables en héritent toutes. "
            f"Ajoutez cette table à un groupe rattaché à « {schema} », ou "
            f"créez-en un."
        )

    # La base suit exactement la même règle que le schéma, pour exactement la
    # même raison — à ceci près qu'elle est bien plus souvent tue. Comparer les
    # deux `database:` écrits laissait passer le cas le plus courant : un groupe
    # qui n'en déclare pas lit la base de la cible, et y ajouter une table d'une
    # autre base écrivait un YAML qui envoie dbt lire ailleurs. `dev.main.orders`
    # au lieu d'`another_database.main.orders`, sans une ligne d'avertissement
    # si la première existe.
    #
    # `effective_database` est cette base héritée, lue dans le manifeste. À
    # défaut — un groupe encore vide, que dbt ne connaît pas — il ne reste que
    # le YAML, et on en revient à comparer ce qui est écrit.
    #
    # La comparaison ignore la casse, comme le fait déjà l'inventaire :
    # `information_schema` rend `ANALYTICS` là où le profil écrit `analytics`,
    # et refuser là-dessus bloquerait une déclaration juste.
    wanted = (database or "").strip()
    effective = (
        str(group.get("database") or "").strip() or (effective_database or "").strip()
    )
    if wanted and effective and wanted.casefold() != effective.casefold():
        raise DatasetError(
            f"Le groupe de sources « {source_name} » lit la base "
            f"« {effective} », pas « {wanted} » : ses tables en héritent "
            f"toutes. Ajoutez cette table à un groupe rattaché à "
            f"« {wanted} », ou créez-en un."
        )


def declare_source(
    settings: Settings,
    *,
    source_name: str,
    schema: str,
    tables: list[dict],
    description: str = "",
    database: str | None = None,
    manifest=None,
) -> dict:
    """Ajoute des tables de l'entrepôt à un groupe de sources.

    N'écrase jamais une table déjà déclarée : on complète, on ne remplace pas.

    `manifest` sert à connaître la base que dbt donne déjà aux tables d'un
    groupe existant, celle héritée de la cible comprise. Sans lui, un groupe
    qui ne déclare pas sa base accepte une table de n'importe quelle autre.
    """
    source_name = _check_name(source_name, "nom de la source")
    schema = (schema or "").strip()
    if not schema:
        raise DatasetError("Le schéma de la source est obligatoire.")
    if not tables:
        raise DatasetError("Sélectionnez au moins une table.")

    rel, data = _sources_file(settings, source_name)
    if not data:
        data = files.load_yaml(settings, rel) or {}
    data.setdefault("version", 2)
    if not data.get("sources"):
        data["sources"] = []

    group = None
    for entry in data["sources"]:
        if isinstance(entry, dict) and entry.get("name") == source_name:
            group = entry
            break
    if group is None:
        group = {"name": source_name, "schema": schema}
        if database:
            group["database"] = database
        if description:
            group["description"] = DQ(description)
        group["tables"] = []
        data["sources"].append(group)
    else:
        _check_group_matches(
            group,
            source_name,
            schema,
            database,
            group_database(manifest, source_name),
        )
        group.setdefault("schema", schema)
        if description and not group.get("description"):
            group["description"] = DQ(description)
        declared = group.get("tables")
        # `tables:` absent ou vide se complète sans rien perdre. Une autre
        # forme — `tables: orders`, une table YAML — est refusée plutôt
        # qu'écrasée : c'est du travail écrit à la main, et `.append()` dessus
        # ne rendait qu'une panne sans dire quel fichier réparer.
        if declared is None:
            group["tables"] = []
        elif not isinstance(declared, list):
            raise DatasetError(
                f"Le groupe de sources « {source_name} » déclare un "
                f"« tables: » qui n'est pas une liste, dans {rel}. Corrigez le "
                f"fichier : l'atelier ne réécrit pas ce qu'il ne sait pas lire."
            )

    existing = {t.get("name") for t in group["tables"] if isinstance(t, dict)}
    added: list[str] = []
    skipped: list[str] = []
    for t in tables:
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        if name in existing:
            skipped.append(name)
            continue
        entry: dict[str, Any] = {"name": name}
        if t.get("description"):
            entry["description"] = DQ(str(t["description"]))
        group["tables"].append(entry)
        added.append(name)
        # Le nom rejoint l'ensemble des connus : sans cela, une requête qui
        # nomme deux fois la même table écrivait deux entrées identiques —
        # annoncées toutes les deux « ajoutées » — et le parse dbt refusait
        # ensuite la source entière.
        existing.add(name)

    files.dump_yaml(settings, rel, data)
    return {"path": rel, "added": added, "skipped": skipped, "source": source_name}


# ------------------------------------------------------------ couche suggérée


LAYER_REASONS = {
    "staging": "l'entrée est une source ou un seed : la couche staging la nettoie "
    "et la type, une vue par table brute",
    "intermediate": "les entrées sont des modèles de staging : le résultat est une "
    "étape de calcul, pas encore un objet métier",
    "marts": "les entrées sont déjà des modèles retravaillés : le résultat est un "
    "objet métier, exposable",
}


def layer_of(path: str, model_paths: list[Path], project_dir: Path) -> str:
    """La couche d'un modèle = son premier sous-dossier sous model-paths."""
    for base in model_paths:
        try:
            rel = Path(path).relative_to(base.relative_to(project_dir))
        except ValueError:
            continue
        parts = rel.parts
        return parts[0] if len(parts) > 1 else ""
    parts = Path(path).parts
    return parts[1] if len(parts) > 2 else ""


def suggest_layer(settings: Settings, manifest, inputs: list[dict]) -> dict:
    """Propose une couche à partir de la nature des entrées.

    C'est la convention dbt (staging → intermediate → marts), pas une règle de
    l'outil : la proposition reste modifiable.
    """
    model_paths = settings.model_paths()
    kinds: list[str] = []

    for inp in inputs or []:
        if inp.get("source_name") or inp.get("table"):
            kinds.append("raw")
            continue
        # Par le nom seul, un homonyme de paquet répondait pour le modèle
        # choisi, et la couche proposée venait de son chemin à lui.
        node = graph.input_node_id(manifest, inp, settings.project_name)
        if node is None:
            continue
        rt = str(getattr(node, "resource_type", ""))
        if rt.endswith("seed") or rt.endswith("snapshot"):
            kinds.append("raw")
            continue
        layer = layer_of(
            getattr(node, "original_file_path", ""), model_paths, settings.project_dir
        )
        kinds.append(layer or "model")

    if not kinds:
        return {"layer": "staging", "reason": ""}
    if "raw" in kinds:
        layer = "staging"
    elif all(k in ("staging", "stg") for k in kinds):
        layer = "intermediate"
    else:
        layer = "marts"
    return {"layer": layer, "reason": LAYER_REASONS.get(layer, "")}


def layers(settings: Settings) -> list[dict]:
    """Les couches réellement présentes dans le projet, avec la matérialisation
    que `dbt_project.yml` leur impose."""
    base = next((p for p in settings.model_paths()), settings.project_dir / "models")
    on_disk: list[str] = []
    if base.is_dir():
        for child in sorted(base.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                on_disk.append(child.name)

    # Ordre du flux, pas ordre alphabétique : staging vient avant marts.
    found = ["staging", "intermediate", "marts"]
    found += [d for d in on_disk if d not in found]

    cfg = (settings.project_yml.get("models") or {}).get(settings.project_name) or {}
    out = []
    for name in found:
        node = cfg.get(name) or {}
        out.append(
            {
                "name": name,
                "exists": (base / name).is_dir(),
                "materialized": (
                    node.get("+materialized") if isinstance(node, dict) else None
                ),
            }
        )
    out.append({"name": "", "exists": True, "materialized": None})
    return out

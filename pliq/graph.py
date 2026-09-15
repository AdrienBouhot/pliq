"""Le Flow : un graphe biparti dataset → recipe → dataset.

dbt fusionne la transformation et sa sortie dans un seul fichier. Pour retrouver
la lecture d'un ETL visuel, on ré-expose la transformation comme un nœud à part
entière, posé entre les datasets d'entrée et le dataset produit.

Passé quelques dizaines de modèles, tout afficher ne montre plus rien. On peut
donc **replier** une partie du graphe en un nœud unique, désignée depuis un
nœud du Flow : tout son amont, tout son aval, ou la zone — le dossier dbt —
dont il fait partie. Le graphe est contracté *avant* d'être disposé :
profondeurs, colonnes et routage des arêtes valent alors pour ce qui est
réellement à l'écran, et non pour un graphe complet qu'on masquerait après coup.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable
from typing import Any

from . import recipes as rcp

DS_SIZE = 68  # côté du carré dataset
RC_SIZE = 42  # diamètre du cercle recipe
GROUP_W = 136  # largeur du nœud d'un bloc replié
CELL_W = 148  # largeur réservée au libellé sous un dataset
LABEL_H = 30  # hauteur du libellé, sous la forme du nœud
PITCH_X = 210  # distance entre deux colonnes de datasets
PITCH_Y = 116
PAD_X = 92
PAD_Y = 54
LANE_GAP = 18  # écart entre une arête déviée et les nœuds qu'elle contourne

LAYER_ORDER = ["source", "staging", "intermediate", "marts"]

FOLD_KINDS = ("up", "down", "zone")


def fold_id(kind: str, key: str) -> str:
    return f"fold::{kind}::{key}"


def zone_id(name: str) -> str:
    return fold_id("zone", name)


def _parse_folds(folded: Iterable[str] | None) -> list[tuple[str, str]]:
    """`["up:model.a", "zone:staging"]` → `[("up", "model.a"), ("zone", "staging")]`.

    Un nom nu désigne une zone : c'est la forme la plus ancienne, et celle qui
    peut encore dormir dans le stockage d'un navigateur.
    """
    folds: list[tuple[str, str]] = []
    for raw_text in folded or ():
        spec = str(raw_text).strip()
        if not spec:
            continue
        kind, _, key = spec.partition(":")
        if not key:
            kind, key = "zone", kind
        if kind in FOLD_KINDS and (kind, key) not in folds:
            folds.append((kind, key))
    return folds


def _reach(start: str, adj: dict[str, list[str]]) -> set[str]:
    """Tout ce qu'on atteint depuis `start` en suivant `adj`, `start` exclu."""
    seen: set[str] = set()
    queue = [start]
    while queue:
        for neighbour in adj.get(queue.pop()) or []:
            if neighbour != start and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen


def _build_groups(
    folds: list[tuple[str, str]],
    datasets: dict,
    parents: dict[str, list[str]],
    children: dict[str, list[str]],
    zone_members: dict[str, list[str]],
    names: dict[str, str],
) -> list[dict]:
    """Les blocs repliés, dans l'ordre demandé.

    Deux replis peuvent se recouvrir — dans un DAG, deux marts partagent souvent
    leurs ancêtres. Le premier servi garde le nœud : la règle est arbitraire,
    mais elle est stable, et elle garantit qu'un dataset n'apparaît qu'une fois.
    """
    groups: list[dict] = []
    held: set[str] = set()

    for kind, key in folds:
        if kind == "zone":
            candidates = zone_members.get(key) or []
            label = key
        else:
            # Replier l'amont d'un nœud lui-même replié n'a plus de sens.
            if key not in datasets or key in held:
                continue
            candidates = sorted(_reach(key, parents if kind == "up" else children))
            label = ("amont de " if kind == "up" else "aval de ") + names[key]

        members = sorted(u for u in candidates if u not in held)
        if not members:
            continue
        held.update(members)
        groups.append(
            {
                "id": fold_id(kind, key),
                "spec": f"{kind}:{key}",
                "fold_kind": kind,
                "label": label,
                "members": members,
            }
        )
    return groups


def _layer_of(node) -> str:
    rt = str(getattr(node, "resource_type", ""))
    if rt.endswith("source") or rt.endswith("seed"):
        return "source"
    if rt.endswith("snapshot"):
        return "intermediate"

    path = (getattr(node, "original_file_path", "") or "").replace("\\", "/").lower()
    name = (getattr(node, "name", "") or "").lower()
    for token, layer in (
        ("staging", "staging"),
        ("/stg", "staging"),
        ("intermediate", "intermediate"),
        ("/int", "intermediate"),
        ("marts", "marts"),
        ("mart", "marts"),
        ("core", "marts"),
    ):
        if token in path:
            return layer
    if name.startswith("stg_"):
        return "staging"
    if name.startswith("int_"):
        return "intermediate"
    return "marts"


def _zone_of(node) -> str:
    """La zone d'un dataset : le dossier dbt dont il est issu.

    C'est le regroupement que l'auteur du projet a déjà fait lui-même, dossier
    par dossier — bien plus parlant qu'une couche devinée. Un modèle posé à la
    racine de `models/` n'a pas de dossier à lui : il rejoint sa couche.
    """
    kind = kind_of(node)
    if kind == "source":
        return "sources"
    if kind == "seed":
        return "seeds"
    parts = [
        p
        for p in (getattr(node, "original_file_path", "") or "")
        .replace("\\", "/")
        .split("/")
        if p
    ]
    if len(parts) >= 3:
        return parts[1]
    return _layer_of(node)


def _display_name(node) -> str:
    """Une source se lit `schema.table` : son nom seul ne suffit pas à la situer."""
    name = getattr(node, "name", "") or ""
    if kind_of(node) == "source":
        return f"{getattr(node, 'source_name', '')}.{name}"
    return name


def kind_of(node) -> str:
    rt = str(getattr(node, "resource_type", ""))
    for kind in ("source", "seed", "snapshot", "test", "analysis", "model"):
        if rt.endswith(kind):
            return kind
    return "model"


def _materialized(node) -> str:
    kind = kind_of(node)
    if kind in ("source", "seed"):
        return kind
    cfg = getattr(node, "config", None)
    return getattr(cfg, "materialized", None) or "view"


def _test_name(test_node) -> str:
    meta = getattr(test_node, "test_metadata", None)
    if meta is not None and getattr(meta, "name", None):
        return meta.name
    return (getattr(test_node, "name", "") or "").split(".")[0]


def _layer_rank(layer: str) -> int:
    return LAYER_ORDER.index(layer) if layer in LAYER_ORDER else 9


def belongs_to_project(node, project: str | None) -> bool:
    """Ce nœud est-il déclaré par le projet ouvert, ou par un paquet installé ?

    dbt range dans un seul manifest les nœuds de tous les paquets, et
    `original_file_path` est relatif à la racine du paquet qui les déclare.
    Résolu depuis le projet ouvert, le `models/orders.sql` d'un paquet désigne
    donc le modèle local qui porte le même nom : on lirait, et surtout on
    écrirait, à côté.

    Sans nom de projet — les appels qui n'ont pas la question à poser —, tout
    appartient au projet : c'est l'état d'avant.
    """
    if not project:
        return True
    pkg = str(getattr(node, "package_name", "") or "")
    return not pkg or pkg == project


def _sortable_version(node):
    """De quoi ordonner deux versions : les numéros d'abord, puis le texte.

    Un projet ne mélange pas les deux écritures en pratique ; l'ordre entre un
    numéro et un nom est donc arbitraire, mais il est stable.
    """
    raw_text = getattr(node, "version", None)
    try:
        return (0, int(raw_text), "")
    except (TypeError, ValueError):
        return (1, 0, str(raw_text))


def _within(pool: list, version: str):
    """La version demandée dans ce lot de nœuds homonymes, ou celle que dbt rend."""
    if not pool:
        return None
    if version:
        return next(
            (n for n in pool if str(getattr(n, "version", None)) == version), None
        )
    # Un modèle sans version : il n'y en a qu'un, c'est lui.
    bare = next((n for n in pool if getattr(n, "version", None) is None), None)
    if bare is not None:
        return bare
    # Sinon la dernière, comme `ref('nom')` sans `v=`. `latest_version` est
    # normalement renseigné par dbt ; s'il manque, la plus haute fait foi.
    last = next(
        (
            n
            for n in pool
            if getattr(n, "latest_version", None) is not None
            and str(n.latest_version) == str(getattr(n, "version", None))
        ),
        None,
    )
    return last or max(pool, key=_sortable_version)


def input_node_id(manifest, inp: dict, project: str | None = None):
    """Le nœud de manifeste que désigne une entrée de recipe, ou None.

    Une entrée nomme un dataset par trois choses, et il en faut trois : le nom
    ne suffit pas. Deux paquets peuvent déclarer `orders`, et un modèle
    versionné porte le même nom à toutes ses versions — chercher par le seul
    nom rendait le premier nœud rencontré, c'est-à-dire souvent l'homonyme du
    projet ouvert.

    Une entrée qui ne dit ni paquet ni version désigne ce que `ref('nom')`
    résoudrait, et la résolution suit donc celle de dbt : le projet ouvert
    d'abord, les paquets ensuite, et la dernière version. C'est le cas de
    toutes les recipes écrites avant que l'identité soit portée — dont celles
    qui lisent un modèle que seul un paquet déclare.
    """
    if inp.get("source_name"):
        for node in manifest.sources.values():
            if str(getattr(node, "source_name", "")) == str(
                inp["source_name"]
            ) and getattr(node, "name", None) == inp.get("table"):
                return node
        return None

    package = str(inp.get("package") or "").strip()
    version = str(inp.get("version") or "").strip()
    local, elsewhere = [], []
    for node in manifest.nodes.values():
        # Le type compte : un test ou un snapshot peut porter le nom d'un
        # modèle, et ne dit rien de ce que `ref()` ira chercher.
        if getattr(node, "name", None) != inp.get("ref"):
            continue
        if kind_of(node) not in ("model", "seed", "snapshot"):
            continue
        if belongs_to_project(node, project):
            local.append(node)
        else:
            elsewhere.append(node)

    if package:
        targets_it = [
            n for n in elsewhere if str(getattr(n, "package_name", "") or "") == package
        ]
        return _within(targets_it, version)
    # Le projet d'abord, les paquets seulement s'il ne déclare pas ce nom. Le
    # repli porte sur le nom, jamais sur la version : une v2 demandée que le
    # projet n'a pas ne doit pas être servie par un homonyme de paquet — c'est
    # la confusion d'identité qu'on cherche justement à fermer.
    return _within(local or elsewhere, version)


def _test_dict(uid: str, node) -> dict:
    """Un test dbt, tel que le Flow et les fiches le décrivent."""
    return {
        "id": uid,
        "name": _test_name(node),
        "column": getattr(node, "column_name", None),
        "severity": str(
            getattr(getattr(node, "config", None), "severity", "error")
        ).lower(),
    }


def node_card(manifest, uid: str) -> dict:
    """Ce que le Flow dirait de ce dataset, sans construire le Flow.

    `GET /api/dataset/{uid}` appelait `collect()` pour en extraire une seule
    entrée : tout le graphe disposé, toutes les arêtes routées, à chaque clic
    sur un nœud. Sur un projet de mille modèles, la fiche coûtait autant que le
    Flow entier. Rien de tout cela n'est nécessaire ici — ni colonnes, ni
    positions, ni arêtes.
    """
    node = manifest.nodes.get(uid) or manifest.sources.get(uid)
    if node is None:
        return {}
    tests = [
        _test_dict(tid, t)
        for tid, t in manifest.nodes.items()
        if kind_of(t) == "test"
        and uid in (getattr(getattr(t, "depends_on", None), "nodes", []) or [])
    ]
    return {
        "id": uid,
        "name": _display_name(node),
        "kind": kind_of(node),
        "layer": _layer_of(node),
        "materialized": _materialized(node),
        "tests": tests,
    }


def collect(
    manifest,
    recipe_specs: dict[str, dict] | None = None,
    folded: Iterable[str] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    recipe_specs = recipe_specs or {}
    datasets: dict[str, Any] = {}

    for uid, node in manifest.nodes.items():
        if kind_of(node) in ("test", "analysis"):
            continue
        datasets[uid] = node
    for uid, src in manifest.sources.items():
        datasets[uid] = src

    # Tests rattachés à leur dataset.
    tests: dict[str, list[dict]] = {uid: [] for uid in datasets}
    for uid, node in manifest.nodes.items():
        if kind_of(node) != "test":
            continue
        for parent in list(
            getattr(getattr(node, "depends_on", None), "nodes", []) or []
        ):
            if parent in tests:
                tests[parent].append(_test_dict(uid, node))

    parents: dict[str, list[str]] = {uid: [] for uid in datasets}
    for uid, node in datasets.items():
        for dep in list(getattr(getattr(node, "depends_on", None), "nodes", []) or []):
            if dep in datasets and dep != uid:
                parents[uid].append(dep)

    children: dict[str, list[str]] = {uid: [] for uid in datasets}
    for uid, ps in parents.items():
        for parent in ps:
            children[parent].append(uid)

    layers = {uid: _layer_of(node) for uid, node in datasets.items()}
    names = {uid: _display_name(node) for uid, node in datasets.items()}
    zone_members: dict[str, list[str]] = {}
    for uid, node in datasets.items():
        zone_members.setdefault(_zone_of(node), []).append(uid)

    groups = _build_groups(
        _parse_folds(folded), datasets, parents, children, zone_members, names
    )
    absorbed = {uid: g["id"] for g in groups for uid in g["members"]}

    # Graphe contracté : une zone repliée n'y est plus qu'un nœud, et les
    # dépendances internes à la zone disparaissent avec elle.
    linked: dict[str, list[str]] = {}
    for uid in datasets:
        a = absorbed.get(uid, uid)
        deps = linked.setdefault(a, [])
        for dep in parents.get(uid) or []:
            b = absorbed.get(dep, dep)
            if b != a and b not in deps:
                deps.append(b)

    depth = _depths(list(linked), linked)

    # ---- nœuds dataset
    ds_nodes: list[dict] = []
    for uid, node in datasets.items():
        if uid in absorbed:
            continue
        kind = kind_of(node)
        # Un modèle installé par `dbt deps` se voit, se lit et se référence,
        # mais ne s'édite pas d'ici : son fichier n'est pas dans ce projet.
        external = not belongs_to_project(node, project)
        ds_nodes.append(
            {
                "id": uid,
                "node_type": "dataset",
                "name": names[uid],
                "kind": kind,
                "layer": layers[uid],
                "zone": _zone_of(node),
                "materialized": _materialized(node),
                "description": (getattr(node, "description", "") or "").strip(),
                "path": getattr(node, "original_file_path", "") or "",
                "schema": getattr(node, "schema", "") or "",
                "database": getattr(node, "database", "") or "",
                "relation": getattr(node, "relation_name", None),
                "tags": list(getattr(node, "tags", []) or []),
                "tests": tests.get(uid, []),
                "columns": list((getattr(node, "columns", {}) or {}).keys()),
                "depth": depth.get(uid, 0),
                "external": external,
                "package": str(getattr(node, "package_name", "") or ""),
                # Un modèle versionné porte le même nom à toutes ses versions :
                # le Flow doit pouvoir dire de laquelle il parle, ne serait-ce
                # que pour qu'une entrée de recipe reprenne la bonne.
                "version": (
                    "" if getattr(node, "version", None) is None else str(node.version)
                ),
                "editable": kind == "model" and not external,
                # Ce que replierait un clic droit sur ce nœud. Compté sur le
                # graphe entier : le menu doit annoncer le vrai coût du geste,
                # pas ce qu'il en reste une fois d'autres blocs repliés.
                "up_count": len(_reach(uid, parents)),
                "down_count": len(_reach(uid, children)),
            }
        )

    for g in groups:
        inside = g["members"]
        ds_nodes.append(
            {
                "id": g["id"],
                "node_type": "group",
                "fold": g["spec"],
                "fold_kind": g["fold_kind"],
                "name": g["label"],
                "kind": "group",
                "layer": min((layers[u] for u in inside), key=_layer_rank),
                "count": len(inside),
                "members": inside,
                "member_names": [names[u] for u in inside],
                "tests": [],
                "columns": [],
                "tags": [],
                "depth": depth.get(g["id"], 0),
                "external": False,
                "editable": False,
            }
        )

    _lay_out_datasets(ds_nodes)
    by_id = {n["id"]: n for n in ds_nodes}

    # ---- nœuds recipe + arêtes
    rc_nodes: list[dict] = []
    edges: list[dict] = []

    for uid, node in datasets.items():
        # La recipe d'un modèle replié est absorbée avec lui : la zone tient
        # lieu de transformation.
        if uid in absorbed:
            continue
        # Seul un modèle porte une recipe. Tout le reste — un snapshot d'abord,
        # puisque c'est le seul autre type qui ait des entrées — reçoit ses
        # arêtes directement. Sauter ces nœuds faisait plus que leur retirer
        # une recipe : il ne leur restait plus *aucune* arête entrante, et la
        # lignée affichée se coupait entre un modèle et son snapshot, alors que
        # `up_count` continuait de compter le lien.
        if kind_of(node) != "model":
            for dep in linked.get(uid) or []:
                edges.append({"from": dep, "to": uid})
            continue
        out = by_id[uid]
        name = getattr(node, "name", "")
        # Le script visuel est un fichier de *ce* projet : l'associer par le
        # seul nom donnait au `orders` d'un paquet la recipe du `orders` local,
        # avec ses actions — ouvrir, renommer, supprimer — qui toutes portaient
        # alors sur le modèle local. Un modèle de paquet garde son nœud recipe,
        # parce que la lignée doit rester lisible, mais il n'est jamais géré.
        external = not belongs_to_project(node, project)
        spec = None if external else recipe_specs.get(name)
        if spec:
            rtype = spec.get("type", "prepare")
            nsteps = len(
                [s for s in (spec.get("steps") or []) if s.get("enabled", True)]
            )
        else:
            rtype = rcp.infer_type(getattr(node, "raw_code", "") or "")
            nsteps = 0

        rid = f"recipe::{uid}"
        rc_nodes.append(
            {
                "id": rid,
                "node_type": "recipe",
                "recipe_type": rtype,
                "label": rcp.RECIPE_TYPES.get(rtype, {}).get("label", rtype),
                "model": uid,
                "model_name": name,
                "managed": bool(spec),
                # Ce que l'écran a le droit de proposer sur ce nœud. Le nom ne
                # suffit pas à désigner le modèle produit : c'est `model` qui
                # fait foi, et lui seul.
                "external": external,
                "package": str(getattr(node, "package_name", "") or ""),
                "editable": not external,
                "steps": nsteps,
                "cx": out["cx"] - PITCH_X // 2,
                "cy": out["cy"],
            }
        )

        for dep in linked.get(uid) or []:
            edges.append({"from": dep, "to": rid})
        edges.append({"from": rid, "to": uid})

    # Un bloc replié n'a pas de recipe : ses entrées le rejoignent directement.
    for g in groups:
        for dep in linked.get(g["id"]) or []:
            edges.append({"from": dep, "to": g["id"]})

    nodes = ds_nodes + rc_nodes
    _route_edges(edges, {n["id"]: n for n in nodes})

    width = max((n.get("cx", 0) for n in nodes), default=200) + CELL_W // 2 + PAD_X
    height = max((n.get("cy", 0) for n in nodes), default=200) + DS_SIZE + PAD_Y
    # Une arête déviée peut sortir sous la dernière rangée : le canevas la suit.
    lanes = [e["via"] for e in edges if e.get("via") is not None]
    if lanes:
        height = max(height, max(lanes) + PAD_Y)

    folded_zones = {g["id"] for g in groups if g["fold_kind"] == "zone"}

    def _zone_kind(inside: list[str]) -> str:
        """Un dossier de `models/`, les seeds, ou les sources : ça ne se dit pas
        pareil dans l'interface — et un dossier nommé `seeds/` sous `models/`
        ne doit pas se faire passer pour les vrais seeds."""
        kinds = {kind_of(datasets[u]) for u in inside}
        if kinds == {"seed"}:
            return "seed"
        if kinds == {"source"}:
            return "source"
        return "folder"

    zones = sorted(
        (
            {
                "name": zone,
                "count": len(inside),
                "kind": _zone_kind(inside),
                "folded": zone_id(zone) in folded_zones,
                "layer": min((layers[u] for u in inside), key=_layer_rank),
            }
            for zone, inside in zone_members.items()
        ),
        key=lambda z: (_layer_rank(z["layer"]), z["name"]),
    )

    return {
        "datasets": sorted(ds_nodes, key=lambda n: (n["depth"], n["cy"])),
        "recipes": rc_nodes,
        "edges": edges,
        "zones": zones,
        "folded": [g["spec"] for g in groups],
        "width": max(width, 560),
        "height": max(height, 300),
        "ds_size": DS_SIZE,
        "rc_size": RC_SIZE,
        "group_w": GROUP_W,
        "cell_w": CELL_W,
    }


def _box(node: dict) -> tuple[float, float, float, float]:
    """Encombrement visuel d'un nœud : sa forme, et le libellé posé dessous.

    En largeur on s'en tient à la forme : `CELL_W` est la cellule réservée au
    texte, bien plus large que le carré, et deux cellules voisines se chevauchent
    déjà — la prendre pour obstacle ne laisserait passer aucune arête.
    """
    kind = node.get("node_type")
    if kind == "recipe":
        size, top, wide = RC_SIZE, node["cy"] + (DS_SIZE - RC_SIZE) / 2, RC_SIZE
    elif kind == "group":
        size, top, wide = DS_SIZE, float(node["cy"]), GROUP_W
    else:
        size, top, wide = DS_SIZE, float(node["cy"]), DS_SIZE
    half_w = wide / 2 + 10
    return node["cx"] - half_w, top, node["cx"] + half_w, top + size + LABEL_H


def _anchor_y(node: dict) -> float:
    """Les recipes sont centrées dans la bande d'un dataset : même hauteur d'ancre."""
    return node["cy"] + DS_SIZE / 2


def _crosses(
    box: tuple[float, float, float, float],
    x1: float,
    x2: float,
    ay: float,
    by: float,
) -> bool:
    """Le trait de `(x1, ay)` à `(x2, by)` passe-t-il par-dessus ce nœud ?

    Il faut qu'il l'enjambe de bout en bout : un nœud qui borde seulement l'arête
    — le voisin de la colonne d'arrivée, par exemple — ne la coupe pas.
    """
    if box[0] <= x1 or box[2] >= x2:
        return False
    heights = [ay + (by - ay) * (x - x1) / (x2 - x1) for x in (box[0], box[2])]
    return min(heights) < box[3] + 4 and max(heights) > box[1] - 4


class _Plan:
    """Les boîtes du Flow, rangées comme l'écran les range : par colonne.

    `_route_edges` comparait chaque arête à *tous* les nœuds. C'est un produit
    arêtes × nœuds, payé à chaque construction du Flow — et le Flow se
    reconstruit après chaque enregistrement. Mesuré sur un projet de la forme
    habituelle (staging → intermediate → marts) : 0,19 s à 500 modèles, 2,8 s à
    2 000, 10,7 s à 4 000. Doubler la taille multipliait le temps par presque
    quatre.

    La disposition range pourtant déjà les nœuds en colonnes, et en lignes dans
    chaque colonne. Deux bornes suffisent alors à écarter l'immense majorité des
    boîtes sans les regarder :

    - en abscisse, `_crosses` exige que la boîte tienne *strictement* entre les
      deux bouts de l'arête : seules les colonnes qui tombent dedans comptent ;
    - en ordonnée, le trait a, au droit d'une colonne, une hauteur connue à la
      largeur d'une boîte près : seules les lignes voisines de cette hauteur
      peuvent être enjambées.

    Le résultat est identique à celui du balayage complet — c'est le même
    `_crosses` qui tranche, sur un sur-ensemble des boîtes qu'il aurait pu
    accepter.
    """

    def __init__(self, boxes: dict[str, tuple[float, float, float, float]]):
        self.boxes = boxes
        columns: dict[float, list] = {}
        for uid, box in boxes.items():
            # Le centre, et pas le bord : deux nœuds d'une même colonne n'ont
            # pas la même largeur de boîte, mais bien la même abscisse.
            columns.setdefault((box[0] + box[2]) / 2, []).append((box, uid))
        for column in columns.values():
            column.sort(key=lambda consumed: consumed[0][1])
        self.columns = columns
        self.x_positions = sorted(columns)
        self.tops = {
            cx: [consumed[0][1] for consumed in columns[cx]] for cx in self.x_positions
        }
        # De quoi élargir les bornes juste ce qu'il faut pour ne rien perdre.
        self.half_widest = (
            max((b[2] - b[0]) / 2 for b in boxes.values()) if boxes else 0
        )
        self.half_narrowest = (
            min((b[2] - b[0]) / 2 for b in boxes.values()) if boxes else 0
        )
        self.top_max = max((b[3] - b[1]) for b in boxes.values()) if boxes else 0

        # Pour placer la voie de contournement, il faut le haut le plus haut et
        # le bas le plus bas de tout ce que l'arête longe. Dans une colonne,
        # longer l'arête ne dépend que de la largeur de la boîte — un carré, un
        # cercle de recipe, un bloc replié : trois largeurs, jamais plus — et
        # pas du tout de sa ligne. On prépare donc, par colonne et par largeur,
        # les hauts croissants et les bas décroissants : la réponse est en tête
        # de liste, à deux valeurs près qu'il faut parfois sauter.
        self.group_list: dict[float, list] = {}
        for cx, column in columns.items():
            by_inclusive: dict[float, list] = {}
            for box, uid in column:
                by_inclusive.setdefault((box[2] - box[0]) / 2, []).append((box, uid))
            self.group_list[cx] = [
                (
                    half,
                    sorted((b[1], u) for b, u in batch),
                    sorted(((b[3], u) for b, u in batch), reverse=True),
                )
                for half, batch in by_inclusive.items()
            ]

    def footprint(self, x1: float, x2: float, ends):
        """Le haut et le bas de tout ce que l'arête longe, ses bouts exclus."""
        top = bottom = None
        i = bisect.bisect_left(self.x_positions, x1 - self.half_widest)
        while i < len(self.x_positions) and self.x_positions[i] < x2 + self.half_widest:
            cx = self.x_positions[i]
            i += 1
            for half, tops, bases in self.group_list[cx]:
                if not (cx + half > x1 and cx - half < x2):
                    continue
                for v, uid in tops:
                    if uid not in ends:
                        top = v if top is None else min(top, v)
                        break
                for v, uid in bases:
                    if uid not in ends:
                        bottom = v if bottom is None else max(bottom, v)
                        break
        return top, bottom

    def straddles(self, x1: float, x2: float, ay: float, by: float, ends) -> bool:
        """Cette arête passe-t-elle par-dessus au moins une boîte ?"""
        slope = (by - ay) / (x2 - x1)
        # `_crosses` exige que la boîte tienne strictement entre les deux bouts :
        # il lui faut `cx - demi > x1` et `cx + demi < x2`. La plus étroite des
        # boîtes donne la borne la plus large qui reste sûre — et elle écarte
        # déjà les colonnes de départ et d'arrivée, où *aucune* boîte ne peut
        # convenir et qui sont pourtant les plus peuplées du trajet.
        i = bisect.bisect_right(self.x_positions, x1 + self.half_narrowest)
        while (
            i < len(self.x_positions) and self.x_positions[i] < x2 - self.half_narrowest
        ):
            cx = self.x_positions[i]
            i += 1
            # La hauteur du trait au droit de cette colonne, d'un bord de boîte
            # à l'autre : c'est la fenêtre où une boîte peut être enjambée.
            ya = ay + slope * (max(x1, cx - self.half_widest) - x1)
            yb = ay + slope * (min(x2, cx + self.half_widest) - x1)
            bottom, top = (ya, yb) if ya <= yb else (yb, ya)

            column = self.columns[cx]
            tops = self.tops[cx]
            j = bisect.bisect_left(tops, bottom - 4 - self.top_max)
            while j < len(column) and tops[j] <= top + 4:
                box, uid = column[j]
                j += 1
                if uid not in ends and _crosses(box, x1, x2, ay, by):
                    return True
        return False


def _route_edges(edges: list[dict], by_id: dict[str, dict]) -> None:
    """Fait contourner aux arêtes les nœuds qu'elles traverseraient.

    Une jointure lit souvent un dataset situé plusieurs colonnes en amont. Tracée
    droit, cette arête passe derrière les nœuds intermédiaires : on ne voit plus
    qu'un seul chemin entrer dans la recipe, alors qu'il y en a deux. On lui
    réserve donc une voie libre, au-dessus ou en dessous de ce qu'elle enjambe.
    """
    boxes = {uid: _box(n) for uid, n in by_id.items()}
    plan = _Plan(boxes)

    for edge in edges:
        src, dst = by_id.get(edge["from"]), by_id.get(edge["to"])
        if src is None or dst is None:
            continue
        x1, x2 = src["cx"], dst["cx"]
        if x2 <= x1:
            continue

        ay, by = _anchor_y(src), _anchor_y(dst)
        ends = (edge["from"], edge["to"])
        if not plan.straddles(x1, x2, ay, by, ends):
            continue

        # Tout ce qui longe le trajet : la voie de contournement doit l'éviter.
        top, bottom = plan.footprint(x1, x2, ends)
        above = top - LANE_GAP
        below = bottom + LANE_GAP
        mid = (ay + by) / 2
        use_above = above >= LANE_GAP and (mid - above) <= (below - mid)
        edge["via"] = int(above if use_above else below)


def _depths(uids: list[str], parents: dict[str, list[str]]) -> dict[str, int]:
    """La distance de chaque nœud à sa source la plus lointaine.

    Sur une pile explicite, et pas sur celle de Python : la version récursive
    ouvrait deux cadres par niveau de lignée, et une chaîne de 600 modèles
    présentée de l'aval vers l'amont — l'ordre du manifest décide — dépassait
    la limite de 1 000. `collect()` levait alors `RecursionError`, et le projet
    n'avait plus de Flow du tout. Relever la limite globale n'aurait fait que
    déplacer le seuil.

    Chaque nœud est empilé deux fois : une fois pour pousser ses parents, une
    seconde — le marqueur `climbs` — pour se calculer une fois qu'ils sont
    connus. Un parent encore en cours de visite est un cycle : il compte pour
    zéro plutôt que de faire tourner le parcours. Les replis peuvent en créer
    un en contractant une zone, et le Flow doit sortir quand même.
    """
    depth: dict[str, int] = {}
    in_progress: set[str] = set()

    for start_point in uids:
        if start_point in depth:
            continue
        stack: list[tuple[str, bool]] = [(start_point, False)]
        while stack:
            uid, climbs = stack.pop()
            if climbs:
                in_progress.discard(uid)
                # Un parent absent de `depth` est un ancêtre qu'on est en train
                # de visiter : c'est le cas du cycle, et il vaut zéro.
                depth[uid] = (
                    max((depth.get(p, 0) for p in parents.get(uid) or []), default=-1)
                    + 1
                )
                continue
            if uid in depth or uid in in_progress:
                continue
            in_progress.add(uid)
            stack.append((uid, True))
            stack.extend((p, False) for p in parents.get(uid) or [])
    return depth


def _lay_out_datasets(nodes: list[dict]) -> None:
    cols: dict[int, list[dict]] = {}
    for n in nodes:
        cols.setdefault(n["depth"], []).append(n)

    tallest = max((len(g) for g in cols.values()), default=1)
    span = tallest * PITCH_Y

    for d, group in cols.items():
        group.sort(key=lambda n: (_layer_rank(n["layer"]), n["name"]))
        offset = (span - len(group) * PITCH_Y) / 2
        for row, n in enumerate(group):
            n["cx"] = PAD_X + d * PITCH_X
            n["cy"] = int(PAD_Y + row * PITCH_Y + offset)

"""Les projets dbt connus de l'atelier : les lister, en créer, les oublier.

Un « projet » ici n'est rien d'autre qu'un dossier contenant un
`dbt_project.yml`. L'atelier n'invente aucun format : le registre local ne sert
qu'à retrouver les dossiers d'un lancement à l'autre.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import config
from .atomic import write_text_atomically, file_lock

REGISTRY = Path.home() / ".pliq" / "projects.json"
NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# Profondeur de fouille sous un dossier racine : au-delà, on ratisse le disque.
SCAN_DEPTH = 3


class ProjectError(RuntimeError):
    pass


# ------------------------------------------------------------------- registre


def _empty_registry() -> dict:
    return {"projects": [], "last": None}


def _read_registry() -> dict:
    """Le registre, ramené de force à la forme que le reste du module attend.

    Rien ici n'est précieux : un registre illisible se remplace, il ne fait
    pas échouer l'accueil. Mais `{"projects": null}` traversait le
    `setdefault` — la clé *existe* — et cassait `remember` sur un `None` qu'on
    parcourt ; des entrées non textuelles et un `last` d'un autre type se
    propageaient de même.
    """
    if not REGISTRY.exists():
        return _empty_registry()
    try:
        data = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        _set_aside()
        return _empty_registry()
    if not isinstance(data, dict):
        _set_aside()
        return _empty_registry()
    projects = data.get("projects")
    last = data.get("last")
    return {
        "projects": (
            [p for p in projects if isinstance(p, str)]
            if isinstance(projects, list)
            else []
        ),
        "last": last if isinstance(last, str) and last else None,
    }


def _set_aside() -> None:
    """Garde une copie du registre illisible avant de repartir de zéro.

    Il ne contient que des chemins, mais ce sont ceux de l'utilisateur : les
    effacer sans trace lui retire l'accueil qu'il avait, sans recours.
    """
    try:
        REGISTRY.replace(REGISTRY.with_suffix(".json.invalide"))
    except OSError:
        pass


def _write_registry(data: dict) -> None:
    write_text_atomically(REGISTRY, json.dumps(data, indent=2, ensure_ascii=False))


def _edit_registry(mutate) -> None:
    """Relit, modifie et réécrit le registre sous un verrou interprocessus.

    Lire–modifier–écrire sans verrou perd une mise à jour dès que deux
    écritures se croisent : un `scan` qui inscrit chaque projet trouvé pendant
    qu'un autre onglet — ou un autre `pliq` — en oublie un, et la dernière
    écriture écrase la première. « Mon projet a disparu de l'accueil » est un
    bug qu'on ne sait pas expliquer après coup. La relecture a lieu *sous* le
    verrou : c'est elle qui rend l'opération sûre, pas seulement l'écriture.
    """
    with file_lock(REGISTRY):
        data = _read_registry()
        mutate(data)
        _write_registry(data)


def remember(path: Path, *, active: bool = False) -> None:
    """Ajoute un projet au registre (sans doublon) et note le dernier ouvert."""
    path = Path(path).resolve()

    def mutate(data: dict) -> None:
        known = data["projects"]
        if str(path) not in known:
            known.append(str(path))
        if active:
            data["last"] = str(path)

    _edit_registry(mutate)


def forget(path: Path) -> None:
    """Retire le projet de la liste. Ne touche à aucun fichier."""
    path = Path(path).resolve()

    def mutate(data: dict) -> None:
        data["projects"] = [p for p in data["projects"] if p != str(path)]
        if data.get("last") == str(path):
            data["last"] = None

    _edit_registry(mutate)


def last_opened() -> Path | None:
    raw = _read_registry().get("last")
    if not raw:
        return None
    p = Path(raw)
    return p if (p / "dbt_project.yml").exists() else None


# -------------------------------------------------------------------- lecture


def is_project(path: Path) -> bool:
    return (Path(path) / "dbt_project.yml").is_file()


def scan(root: Path, depth: int = SCAN_DEPTH) -> list[Path]:
    """Cherche les projets dbt sous `root`, sans descendre dans les dossiers
    techniques (venv, target, dbt_packages, dossiers cachés)."""
    root = Path(root).resolve()
    skip = {"target", "dbt_packages", "logs", "node_modules", "__pycache__"}
    found: list[Path] = []

    def walk(dir_: Path, level: int) -> None:
        if level > depth:
            return
        if is_project(dir_):
            found.append(dir_)
            return  # un projet dbt n'en contient pas un autre
        try:
            children = sorted(dir_.iterdir())
        except (OSError, PermissionError):
            return
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name.startswith(".") or child.name in skip:
                continue
            walk(child, level + 1)

    walk(root, 0)
    return found


def _count(dir_: Path, suffix: str) -> int:
    if not dir_.is_dir():
        return 0
    return sum(1 for _ in dir_.rglob(f"*{suffix}"))


@dataclass
class Summary:
    path: Path
    name: str
    profile: str
    adapter: str | None
    target: str | None
    targets: list[str]
    models: int
    seeds: int
    snapshots: int
    database: str | None
    database_size: int | None
    last_run: float | None
    error: str | None

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.name,
            "profile": self.profile,
            "adapter": self.adapter,
            "target": self.target,
            "targets": self.targets,
            "models": self.models,
            "seeds": self.seeds,
            "snapshots": self.snapshots,
            "database": self.database,
            "database_size": self.database_size,
            "last_run": self.last_run,
            "error": self.error,
        }


def describe(
    path: Path,
    explicit_profiles_dir: str | None = None,
    explicit_target: str | None = None,
) -> Summary:
    """Fiche d'un projet, lue sans démarrer dbt (le Home doit rester rapide).

    La résolution des profils et de la cible est celle de l'atelier, partagée
    dans `config` : l'accueil lisait, lui, « le projet d'abord, sinon ~/.dbt »
    sans rendre le Jinja, sans regarder `--profiles-dir` ni `DBT_TARGET`, et
    en prenant une cible arbitraire parmi les sorties. Un profil déclaré par
    `env_var()` s'affichait « absent », et la cible annoncée pouvait n'être
    pas celle sur laquelle l'ouverture allait construire.
    """
    path = Path(path).resolve()
    blank = Summary(
        path=path,
        name=path.name,
        profile="",
        adapter=None,
        target=None,
        targets=[],
        models=0,
        seeds=0,
        snapshots=0,
        database=None,
        database_size=None,
        last_run=None,
        error=None,
    )
    if not is_project(path):
        blank.error = "dbt_project.yml introuvable"
        return blank

    settings = config.settings_for(path, explicit_profiles_dir, explicit_target)
    try:
        # Une seule lecture, qui lève une phrase plutôt qu'un `AttributeError`
        # si le document n'est pas une table : un `dbt_project.yml` qui est une
        # liste est un YAML valide, et il faisait tomber toute la liste des
        # projets — pas seulement la fiche de celui-là.
        proj = settings.project_yml
    except (OSError, config.ConfigError) as exc:
        blank.error = f"dbt_project.yml illisible : {exc}"
        return blank

    name = settings.project_name
    profile_name = settings.profile_name

    def folders(key: str, default: str) -> list[Path]:
        return [path / d for d in config.declared_paths(proj.get(key), [default])]

    model_dirs = folders("model-paths", "models")
    seed_dirs = folders("seed-paths", "seeds")
    snap_dirs = folders("snapshot-paths", "snapshots")

    try:
        outputs = settings.outputs()
        target = settings.active_target() if outputs else None
        cfg = settings.target_config()
        targets = settings.targets()
        base = settings.database_path()
    except (OSError, config.ConfigError) as exc:
        blank.name = name
        blank.profile = profile_name
        blank.error = f"profiles.yml illisible : {exc}"
        return blank

    database = None
    db_size = None
    if base is not None:
        database = str(base)
        if base.exists():
            db_size = base.stat().st_size

    results = settings.target_dir / "run_results.json"

    return Summary(
        path=path,
        name=name,
        profile=profile_name,
        adapter=cfg.get("type"),
        target=target,
        targets=targets,
        models=sum(_count(d, ".sql") for d in model_dirs),
        seeds=sum(_count(d, ".csv") for d in seed_dirs),
        snapshots=sum(_count(d, ".sql") for d in snap_dirs),
        database=database,
        database_size=db_size,
        last_run=results.stat().st_mtime if results.exists() else None,
        error=None if outputs else f"aucun profil « {profile_name} » dans profiles.yml",
    )


def catalog(
    extra_roots: list[Path] | None = None,
    depth: int = 1,
    explicit_profiles_dir: str | None = None,
    explicit_target: str | None = None,
) -> list[dict]:
    """Les projets à montrer sur l'écran d'accueil : ceux du registre, plus les
    voisins immédiats du projet ouvert.

    Le balayage implicite reste à un niveau : l'accueil doit s'afficher tout de
    suite, même si le projet vit dans un dossier très peuplé. La fouille en
    profondeur est réservée à « Ajouter un dossier existant ».
    """
    seen: dict[str, Path] = {}
    for raw in _read_registry()["projects"]:
        p = Path(raw)
        if is_project(p):
            seen[str(p.resolve())] = p.resolve()
    for root in extra_roots or []:
        for p in scan(root, depth=depth):
            seen.setdefault(str(p), p)

    # Une fiche illisible ne doit pas emporter la liste : l'accueil est
    # justement l'écran depuis lequel on va réparer le projet fautif.
    out = []
    for p in seen.values():
        try:
            out.append(describe(p, explicit_profiles_dir, explicit_target).as_dict())
        except Exception as exc:  # noqa: BLE001 — une fiche, pas tout l'accueil
            out.append(
                Summary(
                    path=p,
                    name=p.name,
                    profile="",
                    adapter=None,
                    target=None,
                    targets=[],
                    models=0,
                    seeds=0,
                    snapshots=0,
                    database=None,
                    database_size=None,
                    last_run=None,
                    error=f"projet illisible : {exc}",
                ).as_dict()
            )
    out.sort(key=lambda d: (-(d["last_run"] or 0), d["name"]))
    return out


# -------------------------------------------------------------------- création


PROJECT_YML = """\
name: '{name}'
version: '1.0.0'
config-version: 2

profile: '{name}'

model-paths: ["models"]
analysis-paths: ["analyses"]
test-paths: ["tests"]
seed-paths: ["seeds"]
macro-paths: ["macros"]
snapshot-paths: ["snapshots"]

clean-targets:
  - "target"
  - "dbt_packages"

models:
  {name}:
    staging:
      +materialized: view
      +schema: staging
    intermediate:
      +materialized: view
      +schema: intermediate
    marts:
      +materialized: table
      +schema: marts
"""

PROFILES_YML = """\
{name}:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: dev.duckdb
      threads: 4
    prod:
      type: duckdb
      path: prod.duckdb
      threads: 4
"""

GITIGNORE = """\
target/
dbt_packages/
logs/
*.duckdb
*.duckdb.wal
"""

README = """\
# {name}

Projet dbt Core créé avec l'atelier Pliq.

```bash
dbt seed     # charge les CSV de seeds/
dbt build    # construit et teste tout le projet
```

Les modèles sont rangés en trois couches :

- `models/staging` — une vue par table source, renommage et typage seulement ;
- `models/intermediate` — jointures et calculs intermédiaires ;
- `models/marts` — les tables métier, celles qu'on expose.
"""

SOURCES_YML = """\
version: 2

sources: []
"""


def create(parent: Path, name: str, *, adapter: str = "duckdb") -> Path:
    """Crée un projet dbt minimal mais complet, prêt pour `dbt build`."""
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ProjectError(
            "Le nom du projet doit être en minuscules, sans accent ni espace "
            "(lettres, chiffres et « _ »), et commencer par une lettre."
        )
    if adapter != "duckdb":
        raise ProjectError(
            "L'atelier ne sait créer que des projets DuckDB pour l'instant. "
            "Pour un autre entrepôt, créez le projet avec « dbt init », "
            "puis ajoutez-le ici avec « Ajouter un projet existant »."
        )

    parent = Path(parent).expanduser().resolve()
    if not parent.is_dir():
        raise ProjectError(f"Le dossier parent n'existe pas : {parent}")

    root = parent / name
    if root.exists() and any(root.iterdir()):
        raise ProjectError(f"Le dossier {root} existe déjà et n'est pas vide.")

    for sub in (
        "models/staging",
        "models/intermediate",
        "models/marts",
        "seeds",
        "macros",
        "tests",
        "snapshots",
        "analyses",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
        (root / sub / ".gitkeep").touch()

    (root / "dbt_project.yml").write_text(PROJECT_YML.format(name=name))
    (root / "profiles.yml").write_text(PROFILES_YML.format(name=name))
    (root / ".gitignore").write_text(GITIGNORE)
    (root / "README.md").write_text(README.format(name=name))
    (root / "models" / "sources.yml").write_text(SOURCES_YML)

    remember(root)
    return root


def delete(path: Path, confirm: str) -> None:
    """Efface un projet du disque. Exige le nom du dossier en confirmation."""
    path = Path(path).resolve()
    if not is_project(path):
        raise ProjectError(f"{path} n'est pas un projet dbt — rien n'a été supprimé.")
    if confirm != path.name:
        raise ProjectError(
            f"Confirmation attendue : tapez « {path.name} » pour supprimer ce dossier."
        )
    if path == Path.home() or path.parent == path:
        raise ProjectError("Refus de supprimer ce dossier.")
    # L'effacement d'abord, l'oubli seulement s'il a réussi. Dans l'autre
    # sens, un `rmtree` qui lève — dossier verrouillé, montage réseau, droits —
    # laissait un projet toujours sur le disque mais sorti du registre : la
    # route rend bien « Suppression impossible », et l'accueil ne le retrouve
    # plus. L'inverse est sans gravité, `catalog()` filtrant par `is_project()`
    # et `last_opened()` vérifiant l'existence : une entrée devenue fantôme ne
    # se voit nulle part.
    shutil.rmtree(path)
    forget(path)


def humanize(ts: float | None) -> str:
    if not ts:
        return ""
    delta = time.time() - ts
    if delta < 60:
        return "à l'instant"
    if delta < 3600:
        return f"il y a {int(delta // 60)} min"
    if delta < 86400:
        return f"il y a {int(delta // 3600)} h"
    return f"il y a {int(delta // 86400)} j"

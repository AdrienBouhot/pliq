"""Les scripts visuels sur le disque : `.pliq/recipes/<nom>.yml`.

Le `.sql` reste la source de vérité ; la recipe n'est que la décomposition
qui a servi à l'écrire. Ce qui vit dans le YAML dbt — description et tests de
colonnes — n'est volontairement pas recopié ici.
"""

from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML

from ..atomic import write_text_atomically
from .errors import RecipeError
from .names import validate_name
from .processors import PROCESSORS

RECIPE_DIR = ".pliq/recipes"

_yaml = YAML()


# ------------------------------------------------------------------ stockage


def recipe_file(project_dir: Path, name: str) -> Path:
    """Le script visuel de cette recipe, en refusant toute sortie du projet.

    Valider le nom ne suffit pas. Il ne porte ni séparateur ni `..` — aucune
    traversée n'est possible par là — mais le chemin peut sortir du projet sans
    qu'aucun nom ne soit en cause : un `.pliq` posé en lien symbolique vers un
    dossier voisin, et l'atelier lit et écrit dehors sans rien dire. Le cas vaut
    aussi pour un seul `.yml` lié.

    C'est le confinement que `files.safe_path` applique déjà aux SQL et aux YAML
    du projet dbt ; le stockage des recipes y échappait. Tout passe par ici —
    lecture, écriture, suppression — donc le contrôle y tient en un seul point.
    """
    path = project_dir / RECIPE_DIR / f"{validate_name(name)}.yml"
    root = project_dir.resolve()
    # `resolve()` suit les liens de chaque segment, et n'exige pas que le
    # fichier existe : ce qui n'existe pas encore est simplement recollé au bout
    # du chemin réel. C'est ce qu'il faut pour contrôler avant d'écrire.
    resolved = path.resolve()
    if root not in resolved.parents:
        raise RecipeError(
            f"Le stockage des recipes sort du projet : « {RECIPE_DIR}/"
            f"{validate_name(name)}.yml » mène à {resolved}. Retirez le lien "
            f"symbolique avant de continuer."
        )
    return path


# Ce que la recipe ne stocke pas, bien que l'éditeur le montre : la description
# et les tests de colonnes.
#
# Ils vivent dans le YAML dbt du projet — c'est lui que dbt lit, lui que la
# fiche dataset édite, lui qui suit le `patch_path`. En garder une copie ici
# faisait deux vérités : réenregistrer une recipe sans y toucher réappliquait
# sa copie périmée et annulait ce que la fiche dataset venait d'écrire.
DOC_FIELDS = ("description", "columns")


def stored_spec(spec: dict) -> dict:
    """La recipe telle qu'elle s'écrit sur le disque, sans la doc dbt."""
    out: dict = {}
    for key, value in spec.items():
        if key == "output" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if k not in DOC_FIELDS}
        out[key] = value
    return out


def save_recipe(project_dir: Path, spec: dict) -> str:
    path = recipe_file(project_dir, spec.get("name", ""))
    buf = io.StringIO()
    _yaml.dump(stored_spec(spec), buf)
    # Temporaire puis remplacement : une coupure au milieu de l'écriture
    # laissait un script visuel tronqué, que la lecture suivante refusait —
    # et le Flow entier devenait indisponible pour un seul fichier.
    write_text_atomically(path, buf.getvalue())
    return str(path.relative_to(project_dir))


def load_recipe(project_dir: Path, name: str) -> dict | None:
    """Le script visuel d'un modèle, ou `None` s'il n'y en a pas d'utilisable.

    Le contrat est `dict | None`, et il était tenu par optimisme : un fichier
    dont la racine est une liste ou un scalaire — YAML valide, script
    inutilisable — était rendu tel quel, et le `.get()` de l'appelant tombait
    en `AttributeError`. Un seul fichier mal formé dans `.pliq/recipes` rendait
    alors tout le Flow indisponible.

    `readonly_reason` dit *pourquoi* il n'y a rien, pour qui a besoin de
    distinguer « pas de recipe » de « recipe illisible ».
    """
    return _read_recipe(project_dir, name)[0]


def readonly_reason(project_dir: Path, name: str) -> str | None:
    """Pourquoi ce script visuel n'a pas pu être lu, ou `None` si tout va bien.

    Absent et illisible se confondaient : un fichier cassé se présentait comme
    « ce modèle n'a pas de recipe », ce qui invite à en créer une par-dessus.
    """
    return _read_recipe(project_dir, name)[1]


def _read_recipe(project_dir: Path, name: str) -> tuple[dict | None, str | None]:
    try:
        path = recipe_file(project_dir, name)
    except RecipeError as exc:
        return None, str(exc)
    if not path.exists():
        return None, None
    try:
        data = _yaml.load(path.read_text())
    except Exception as exc:  # noqa: BLE001 — ruamel a sa propre famille
        detail = str(exc).strip().splitlines()
        return None, (
            f"« {RECIPE_DIR}/{name}.yml » n'est pas un YAML valide : "
            f"{detail[0] if detail else type(exc).__name__}"
        )
    if data is None:
        return None, None
    if not isinstance(data, dict):
        return None, (
            f"« {RECIPE_DIR}/{name}.yml » ne contient pas un script de recipe "
            f"(attendu : des clés, lu : {type(data).__name__})."
        )
    return _plain(data), None


def delete_recipe(project_dir: Path, name: str) -> None:
    try:
        path = recipe_file(project_dir, name)
    except RecipeError:
        return
    if path.exists():
        path.unlink()


def list_recipes(project_dir: Path) -> dict[str, dict]:
    """Tous les scripts visuels lisibles du projet, indexés par nom."""
    return recipes_and_errors(project_dir)[0]


def recipes_and_errors(
    project_dir: Path,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Les scripts lisibles, et le diagnostic de ceux qui ne le sont pas.

    Un fichier cassé ne doit pas emporter le reste du projet : le Flow
    continue de s'afficher, et l'écran peut dire lequel aller réparer.
    """
    out: dict[str, dict] = {}
    errors: dict[str, str] = {}
    folder = project_dir / RECIPE_DIR
    if not folder.exists():
        return out, errors
    for f in sorted(folder.glob("*.yml")):
        spec, reason = _read_recipe(project_dir, f.stem)
        if spec:
            out[f.stem] = spec
        elif reason:
            errors[f.stem] = reason
    return out, errors


def _plain(obj):
    """ruamel → structures Python simples, sérialisables en JSON."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    if isinstance(obj, str):
        return str(obj)
    return obj


def library() -> list[dict]:
    """Catalogue des processeurs pour la bibliothèque de l'interface."""
    return [
        {k: v for k, v in proc.items() if k != "fn"} for proc in PROCESSORS.values()
    ]


def infer_type(sql: str) -> str:
    """Devine le type de recipe d'un modèle écrit à la main."""
    low = sql.lower()
    if " join " in low:
        return "join"
    if "group by" in low:
        return "group"
    if "union" in low:
        return "stack"
    return "sql"

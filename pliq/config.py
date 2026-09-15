"""Configuration de l'atelier : où vit le projet dbt, quelle cible utiliser."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(RuntimeError):
    """Un fichier de configuration du projet est là, mais inexploitable.

    Distinct de l'absence : un `dbt_project.yml` manquant rend `{}` et
    l'atelier continue. Un `dbt_project.yml` qui est une liste, lui, ne se
    lit pas — et c'est une phrase à montrer, pas un 500.
    """


def _yaml_mapping(path: Path) -> dict:
    """Le document YAML de `path`, en exigeant une table au premier niveau.

    `safe_load(...) or {{}}` ne rattrapait que le document vide. Une liste ou un
    scalaire — un YAML parfaitement valide — traversait, et le `.get()` suivant
    levait un `AttributeError` jusqu'à la route : 500 sans un mot sur le
    fichier à réparer. Même contrôle et même phrase que `files._load_yaml`.
    """
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        detail = str(exc).strip().splitlines()
        raise ConfigError(
            f"« {path.name} » n'est pas un YAML valide : "
            f"{detail[0] if detail else type(exc).__name__}"
        ) from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"« {path.name} » ne contient pas un document YAML de premier "
            f"niveau (attendu : des clés, lu : {type(data).__name__})."
        )
    return data


def declared_paths(raw: object, default: list[str]) -> list[str]:
    """Normalise une clé `*-paths` du `dbt_project.yml` en liste de chemins.

    dbt accepte `model-paths: models` aussi bien que `model-paths: [models]`.
    La boucle d'origine itérait alors sur les *caractères* de la chaîne, et
    l'atelier écrivait ses modèles dans `m/` — un dossier que dbt ne lit
    jamais. L'écran annonçait « enregistré », le parse suivant ne voyait rien,
    et le renommage comme le contrôle d'homonymie cherchaient au même mauvais
    endroit.
    """
    if raw is None or raw == "":
        raw = default
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    out: list[str] = []
    for p in raw:
        if isinstance(p, bytes):
            p = p.decode("utf-8", "replace")
        rel = str(p).strip().strip("/")
        if rel and rel != "." and rel not in out:
            out.append(rel)
    return out


def render_profiles(raw: dict) -> dict:
    """Le `profiles.yml` tel que dbt le lit : Jinja résolu.

    Un `target:` ou un `schema:` peut être une expression — `{{ env_var(...) }}`
    est la façon habituelle de garder un secret hors du dépôt, et de faire
    dépendre la cible de l'environnement. Lire le YAML brut rendait l'expression
    elle-même : la cible ne correspondait alors à aucune sortie déclarée,
    `target_config()` sortait vide, et l'atelier choisissait une famille SQL
    générique pour un projet que `dbt parse`, lui, acceptait très bien.

    C'est le moteur de dbt qui rend, et pas le nôtre : seul lui couvre
    exactement ce que dbt couvre, à cette version comme à la suivante — le
    traitement des `DBT_ENV_SECRET_` compris, qui évite de faire passer un
    secret dans une réponse de l'API.

    Le rendu peut échouer : Jinja invalide, variable manquante, version de dbt
    qui déplace ces modules. On rend alors le YAML brut, comme avant — afficher
    une expression non résolue vaut mieux que ne rien afficher du tout.
    """
    if not raw:
        return raw
    try:
        from dbt.config.renderer import ProfileRenderer
        from dbt_common.context import InvocationContext, reliably_get_invocation_var

        # `env_var()` réclame le contexte que dbt pose au début de chacune de
        # ses invocations. On pose le nôtre le temps du rendu, puis on rend
        # exactement ce qui était là : le contexte de dbt fige l'environnement
        # à sa création, donc s'y greffer rendrait une variable périmée, et
        # l'écraser durablement retirerait le sien à une commande en cours.
        var = reliably_get_invocation_var()
        token = var.set(InvocationContext(os.environ))
        try:
            return ProfileRenderer({}).render_data(raw)
        finally:
            var.reset(token)
    except Exception:  # noqa: BLE001
        return raw


# dbt lie son option `--target` à ces deux variables, dans cet ordre. Les lire
# ici plutôt que de les deviner garde une seule définition de « la cible » pour
# l'affichage, le dialecte SQL et les commandes.
TARGET_ENV_VARS = ("DBT_ENGINE_TARGET", "DBT_TARGET")


def _target_from_env() -> str | None:
    for name in TARGET_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


@dataclass
class Settings:
    project_dir: Path
    profiles_dir: Path
    target: str | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    # Ce que le lancement a demandé, par opposition à ce qui a été déduit du
    # projet ouvert. La distinction n'a d'intérêt qu'à la bascule : rouvrir un
    # projet doit reconduire un `--profiles-dir` explicite, et surtout pas le
    # `profiles.yml` que l'ancien projet portait à sa racine. Toujours absolu :
    # c'est un chemin de session, qui ne doit pas se relire depuis le projet
    # courant une fois qu'on a changé de répertoire.
    explicit_profiles_dir: str | None = None
    # La cible demandée au lancement, retenue pour la même raison et sous la
    # même règle : elle ne bouge plus. `target` est ce que le projet ouvert a
    # pu en faire — le repli sur le défaut du profil quand il ne la déclare
    # pas. Confondre les deux faisait oublier le `--target` de la session dès
    # la première bascule vers un projet qui l'ignore, et la cible retombait
    # ensuite sur le défaut du profil, qui peut être la production.
    explicit_target: str | None = None

    @property
    def project_name(self) -> str:
        name = self.project_yml.get("name")
        if isinstance(name, (str, int)) and str(name):
            return str(name)
        return self.project_dir.name

    # Deux caches d'instance, invalidés sur la date de modification du
    # fichier. Ces deux propriétés étaient relues — et, pour `profiles.yml`,
    # re-rendues par le moteur Jinja de dbt — à *chaque* accès : un seul
    # `GET /api/project` ouvrait 15 fois `dbt_project.yml` et rendait 6 fois
    # `profiles.yml`, dont quatre pour le seul `active_target()`, qui est
    # appelé aussi par chaque invocation dbt. Le `mtime` garde la propriété
    # qui comptait : un fichier modifié sous l'atelier est relu.
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def _yml_cache(self, key: str, f: Path) -> dict:
        try:
            token = f.stat().st_mtime_ns if f.exists() else None
        except OSError:
            token = None
        cached = self._cache.get(key)
        if cached is not None and cached[0] == token:
            return cached[1]
        data = _yaml_mapping(f)
        if key == "profiles":
            data = render_profiles(data)
        self._cache[key] = (token, data)
        return data

    @property
    def project_yml(self) -> dict:
        return self._yml_cache("project", self.project_dir / "dbt_project.yml")

    @property
    def profile_name(self) -> str:
        name = self.project_yml.get("profile")
        if isinstance(name, (str, int)) and str(name):
            return str(name)
        return self.project_name

    @property
    def profiles_yml(self) -> dict:
        return self._yml_cache("profiles", self.profiles_dir / "profiles.yml")

    def profile(self) -> dict:
        """L'entrée du profil, ou `{}` si elle n'a pas la forme attendue.

        Un `profiles.yml` dont une entrée est une liste est un YAML valide ;
        ce n'est pas une raison pour que la lecture du projet lève.
        """
        input_entry = self.profiles_yml.get(self.profile_name)
        return input_entry if isinstance(input_entry, dict) else {}

    def outputs(self) -> dict:
        outputs_map = self.profile().get("outputs")
        return outputs_map if isinstance(outputs_map, dict) else {}

    def targets(self) -> list[str]:
        return sorted(str(k) for k in self.outputs())

    def active_target(self) -> str:
        """La cible que dbt utilisera vraiment, résolue comme dbt la résout.

        L'ordre est celui de dbt, et il compte : `--target` d'abord, puis
        l'environnement, puis le `target:` du profil. Le sauter faisait diverger
        l'atelier de dbt sans que rien ne le dise — `DBT_TARGET=prod` avec un
        profil réglé sur `dev`, et l'atelier annonçait `dev`, choisissait le
        dialecte de `dev`, pendant que `dbt build` écrivait sur la production.

        L'environnement porte deux noms pour la même option : `--target` de dbt
        est lié aux deux, et n'en privilégier qu'un rouvrait l'écart par l'autre
        porte.

        Le repli final est le mot de dbt — `default` — et pas un nom choisi ici :
        une cible que le profil ne déclare pas doit se voir. Quand il n'y a pas
        de profil du tout, il n'y a rien à contredire, et `dev` reste la valeur
        d'affichage.
        """
        profile_data = self.profile()
        raw_text = profile_data.get("target")
        declared = (
            str(raw_text)
            if isinstance(raw_text, (str, int)) and str(raw_text)
            else None
        )
        return (
            self.target
            or _target_from_env()
            or declared
            or ("default" if profile_data else "dev")
        )

    def command_target(self) -> str | None:
        """La cible à écrire sur la ligne de commande dbt, ou None.

        Épingler la cible résolue fait que la commande construit là où
        l'affichage dit qu'elle construit. On ne l'épingle que si le profil
        qu'on a su lire la déclare : quand l'atelier ne voit pas le profil que
        dbt, lui, voit très bien, imposer un repli d'affichage casserait une
        configuration qui marchait. Dans ce cas c'est dbt qui tranche — et
        `active_target()` suit désormais sa règle, donc les deux disent la même
        chose.
        """
        if self.target:
            return self.target
        target = self.active_target()
        return target if target in self.targets() else None

    def target_config(self) -> dict:
        cfg = self.outputs().get(self.active_target())
        return cfg if isinstance(cfg, dict) else {}

    def database_path(self) -> Path | None:
        """Chemin du fichier DuckDB de la cible active, s'il y en a un."""
        cfg = self.target_config()
        if cfg.get("type") != "duckdb":
            return None
        raw = cfg.get("path")
        if not raw or raw == ":memory:":
            return None
        p = Path(raw)
        return p if p.is_absolute() else (self.project_dir / p).resolve()

    @property
    def target_dir(self) -> Path:
        raw = self.project_yml.get("target-path") or "target"
        return self.project_dir / (str(raw).strip().strip("/") or "target")

    @property
    def manifest_path(self) -> Path:
        return self.target_dir / "manifest.json"

    def packages_needed(self) -> bool:
        """Le projet déclare des paquets, mais ne les a pas installés.

        C'est le mur que rencontre tout projet existant un peu sérieux : sans
        `dbt deps`, le parse échoue et l'atelier n'affiche qu'un message brut
        de dbt. Autant le voir venir, et proposer la commande.
        """
        declare = any(
            (self.project_dir / name).exists()
            for name in ("packages.yml", "dependencies.yml")
        )
        if not declare:
            return False
        path = self.project_yml.get("packages-install-path") or "dbt_packages"
        folder = self.project_dir / (str(path).strip().strip("/") or "dbt_packages")
        return not folder.is_dir() or not any(folder.iterdir())

    def model_dirs(self) -> list[str]:
        """Les `model-paths` du projet, en chemins relatifs au projet.

        On garde le chemin entier : `transform/models` ne se réduit pas à
        `models`, sinon on écrit à côté de ce que dbt va lire.
        """
        return declared_paths(self.project_yml.get("model-paths"), ["models"]) or [
            "models"
        ]

    def model_paths(self) -> list[Path]:
        return [self.project_dir / p for p in self.model_dirs()]

    def code_dirs(self) -> list[str]:
        """Tous les dossiers où dbt lit du SQL ou du YAML de ce projet.

        Un `ref()` ne vit pas que dans `models/` : un test singulier, une
        analyse, une macro, un snapshot en portent aussi, et un test de relation
        posé sur un seed nomme sa cible depuis `seeds/`. Renommer un modèle sans
        regarder là ne casse rien tout de suite — ça casse au `dbt build`.

        On ne descend jamais dans `target/` ni `dbt_packages/` : ce sont des
        copies que dbt refait, pas des sources.
        """
        defaults = {
            "model-paths": ["models"],
            "seed-paths": ["seeds"],
            "test-paths": ["tests"],
            "analysis-paths": ["analyses"],
            "macro-paths": ["macros"],
            "snapshot-paths": ["snapshots"],
        }
        out: list[str] = []
        for key, default in defaults.items():
            for rel in declared_paths(self.project_yml.get(key), default) or default:
                if rel not in out:
                    out.append(rel)
        return out


def profiles_dir_for(
    project_dir: Path, explicit_profiles_dir: str | None = None
) -> Path:
    """Où dbt cherchera `profiles.yml` pour ce projet, dans l'ordre de dbt.

    La même règle que `load_settings`, extraite pour que l'accueil la
    partage : il lisait, lui, « le projet d'abord, sinon ~/.dbt » et ignorait
    `--profiles-dir` comme `DBT_PROFILES_DIR`. Un projet parfaitement
    configuré s'affichait alors « aucun profil ».
    """
    if explicit_profiles_dir:
        return Path(explicit_profiles_dir).expanduser().resolve()
    env = os.environ.get("DBT_PROFILES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if (project_dir / "profiles.yml").exists():
        return project_dir
    return Path.home() / ".dbt"


def settings_for(
    project_dir: Path,
    explicit_profiles_dir: str | None = None,
    explicit_target: str | None = None,
) -> Settings:
    """Un `Settings` de lecture seule pour un projet qu'on ne compte pas ouvrir.

    Sert à l'accueil : il doit décrire un projet exactement comme l'atelier le
    lira à l'ouverture — Jinja des profils rendu, `--target` et les variables
    d'environnement respectés, chemins normalisés. Deux résolutions
    différentes pour la même question donnaient deux réponses, et c'est
    l'accueil qui avait tort.
    """
    project_dir = Path(project_dir).resolve()
    return Settings(
        project_dir=project_dir,
        profiles_dir=profiles_dir_for(project_dir, explicit_profiles_dir),
        target=explicit_target,
        explicit_profiles_dir=explicit_profiles_dir,
        explicit_target=explicit_target,
    )


def discover_project(start: Path) -> Path | None:
    """Trouve un dbt_project.yml : dans `start`, puis dans ses sous-dossiers directs."""
    start = start.resolve()
    if (start / "dbt_project.yml").exists():
        return start
    for child in sorted(start.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            if (child / "dbt_project.yml").exists():
                return child
    return None


def load_settings(
    project_dir: str | None = None,
    profiles_dir: str | None = None,
    target: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> Settings:
    cwd = Path.cwd()

    if project_dir:
        proj = Path(project_dir).expanduser().resolve()
    else:
        env = os.environ.get("DBT_PROJECT_DIR")
        proj = (
            Path(env).expanduser().resolve() if env else (discover_project(cwd) or cwd)
        )

    if not (proj / "dbt_project.yml").exists():
        raise SystemExit(
            f"Aucun dbt_project.yml trouvé dans {proj}.\n"
            f"Lancez l'atelier depuis un projet dbt, ou passez\n"
            f"--project-dir /chemin/du/projet."
        )

    # Le dossier de profils demandé au lancement est retenu *résolu*. Garder la
    # chaîne de la ligne de commande la faisait relire depuis un autre
    # répertoire à la bascule — le `chdir` vers le projet a eu lieu entre-temps
    # — et `--profiles-dir profiles` changeait alors de sens en cours de route.
    # L'environnement compte autant que l'option : `DBT_PROFILES_DIR=profiles`
    # se relisait pareil, puisque la bascule repasse par ici.
    explicit: Path | None = None
    if profiles_dir:
        explicit = Path(profiles_dir).expanduser().resolve()
    elif os.environ.get("DBT_PROFILES_DIR"):
        explicit = Path(os.environ["DBT_PROFILES_DIR"]).expanduser().resolve()
    prof = profiles_dir_for(proj, str(explicit) if explicit else None)

    return Settings(
        project_dir=proj,
        profiles_dir=prof,
        target=target,
        host=host,
        port=port,
        explicit_profiles_dir=str(explicit) if explicit else None,
        explicit_target=target,
    )

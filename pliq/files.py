"""Lecture et écriture des fichiers du projet dbt.

Deux règles : on n'écrit jamais hors du projet, et on ne réécrit jamais un YAML
existant à la machine — ruamel préserve commentaires, ordre et style.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import re
from pathlib import Path
from typing import Any, Callable

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString as DQ

from . import recipes as rcp
from .atomic import write_text_atomically, write_bytes_atomically
from .config import ConfigError, Settings

logger = logging.getLogger("pliq")

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 120


class FileError(ConfigError):
    """Un fichier du projet est introuvable, hors de l'arbre, ou illisible.

    Sous-classe de `ConfigError` : les deux familles se disent à l'écran de la
    même façon — un refus avec le nom du fichier à réparer —, et un seul
    gestionnaire d'exception les couvre donc toutes les deux.
    """


def under_root(resolved: Path, root: Path) -> bool:
    """Ce chemin *déjà résolu* est-il dans l'arbre du projet ?

    Résolu, donc lien symbolique suivi : c'est là où l'écriture atterrira qui
    compte, pas le nom sous lequel on y arrive.
    """
    return resolved == root or str(resolved).startswith(str(root) + "/")


def safe_path(settings: Settings, relative: str) -> Path:
    """Résout un chemin relatif au projet en refusant toute sortie de l'arbre."""
    if not relative:
        raise FileError("Chemin vide.")
    p = (settings.project_dir / relative).resolve()
    if not under_root(p, settings.project_dir.resolve()):
        raise FileError(f"Chemin hors du projet dbt : {relative}")
    return p


def read_text(settings: Settings, relative: str) -> str:
    p = safe_path(settings, relative)
    if not p.exists():
        raise FileError(f"Fichier introuvable : {relative}")
    return p.read_text()


def write_text(settings: Settings, relative: str, content: str) -> Path:
    p = safe_path(settings, relative)
    if not content.endswith("\n"):
        content += "\n"
    write_text_atomically(p, content)
    return p


def digest(path: Path) -> str | None:
    """Empreinte du contenu d'un fichier, ou None s'il n'existe pas.

    Sert à repérer qu'un fichier a bougé depuis qu'un écran l'a lu : on compare
    des empreintes plutôt que des dates, qu'un `git checkout` ou une copie
    remettent à zéro.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


@contextlib.contextmanager
def all_or_nothing(paths: list[Path]):
    """Rend les fichiers dans l'état où ils étaient si le bloc échoue.

    Une sauvegarde touche plusieurs fichiers — le `.sql` du modèle, la recipe,
    le `schema.yml`. Sans ça, une erreur sur le dernier laissait les deux
    premiers dans leur version neuve : le projet n'était plus ni dans l'état
    d'avant, ni dans celui d'après.

    Ce n'est pas une transaction : deux sauvegardes simultanées se marcheraient
    encore dessus. Elles ne le peuvent pas, parce que les routes qui écrivent
    tiennent toutes le même verrou.
    """
    before = {p: (p.read_bytes() if p.exists() else None) for p in dict.fromkeys(paths)}
    try:
        yield
    except BaseException as exc:
        rates = []
        for p, content in before.items():
            try:
                if content is None:
                    p.unlink(missing_ok=True)
                else:
                    write_bytes_atomically(p, content)
            except OSError as oops:  # on ne masque pas l'erreur d'origine…
                rates.append(f"{p.name} ({oops})")
        if rates:
            # …mais on ne tait pas non plus le rollback raté : les fichiers
            # restent alors dans un état mélangé, et c'est la seule chose que
            # l'utilisateur ait besoin de savoir pour aller les réparer.
            logger.error(
                "restauration incomplète après échec (%s) : %s",
                type(exc).__name__,
                ", ".join(rates),
            )
        raise


# ------------------------------------------------------------------ schema.yml


def schema_file_for(model_relative_path: str) -> str:
    """Le schema.yml voisin du modèle (convention du projet d'exemple)."""
    return str(Path(model_relative_path).parent / "schema.yml")


def check_yaml(settings: Settings, relative: str) -> None:
    """Lève une `FileError` si ce YAML du projet ne se lit pas.

    Pour les routes qui touchent plusieurs fichiers : mieux vaut refuser avant
    le premier geste que restaurer après le dernier. `all_or_nothing` rend bien
    les fichiers, mais un refus arrivé avant n'a rien à rendre — et dit au
    passage quel fichier aller réparer.
    """
    path = safe_path(settings, relative)
    if path.exists():
        _load_yaml(path)


def _yaml_error(path: Path, exc: Exception) -> FileError:
    """Une erreur de syntaxe YAML, dite en clair et rattachée à son fichier.

    Sans ça, l'erreur du parseur remontait nue jusqu'à la route : l'écran
    recevait un 500 sans nom de fichier, alors que la seule chose à faire est
    d'aller réparer ce YAML-là.
    """
    detail = str(exc).strip().splitlines()
    return FileError(
        f"« {path.name} » n'est pas un YAML valide : "
        f"{detail[0] if detail else type(exc).__name__}"
    )


def _load_yaml(path: Path) -> dict:
    """Charge un YAML de projet. On n'ajoute aucune section : un fichier de
    sources ne doit pas se retrouver avec un `models:` vide.

    Un fichier illisible est une `FileError`, pas une exception de parseur :
    les routes qui écrivent savent la traduire en refus propre, et renoncent
    avant d'avoir touché quoi que ce soit.
    """
    if not path.exists():
        return {"version": 2}
    try:
        data = _yaml.load(path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001 — ruamel a sa propre famille
        raise _yaml_error(path, exc) from None
    if not isinstance(data, dict):
        raise FileError(
            f"« {path.name} » ne contient pas un document YAML de premier "
            f"niveau (attendu : des clés, lu : {type(data).__name__})."
        )
    if "version" not in data:
        data["version"] = 2
    return data


def _dump_yaml(path: Path, data: Any) -> None:
    buf = io.StringIO()
    _yaml.dump(data, buf)
    write_text_atomically(path, buf.getvalue())


def _tests_key(data: dict) -> str:
    """Respecte la convention déjà en place dans le fichier (tests / data_tests)."""
    for model in data.get("models") or []:
        if "data_tests" in model:
            return "data_tests"
        for col in model.get("columns") or []:
            if "data_tests" in col:
                return "data_tests"
    return "tests"


REF_RE = re.compile(r"^\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*$")

# La cible d'un test de relation est un nom de modèle dbt, et on la recolle
# dans du Jinja : sans ce contrôle, un `to` fabriqué à la main referme le
# `ref()` et en ouvre un autre. L'interface ne propose que des noms du
# manifeste, mais l'API accepte ce qu'on lui donne.
MODEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# `arguments:` regroupe les arguments d'un test sous une clé à part. dbt ne la
# comprend qu'à partir de 1.10.5 ; avant, ces mêmes arguments s'écrivent au
# premier niveau de l'entrée. Voir la documentation des data tests :
# https://docs.getdbt.com/reference/resource-properties/data-tests
#
# On suit le dbt réellement installé plutôt que de supposer le plus récent :
# `pyproject.toml` accepte à partir de 1.9, et écrire `arguments:` là-bas
# produirait un test que dbt refuse de compiler.
ARGUMENTS_SINCE = (1, 10, 5)


def dbt_version() -> tuple[int, ...]:
    """La version de dbt-core installée, en tuple comparable. `()` si inconnue."""
    try:
        from importlib.metadata import version

        raw = version("dbt-core")
    except Exception:  # noqa: BLE001 — dbt absent, ou métadonnées illisibles
        return ()
    digits: list[int] = []
    for part in raw.split(".")[:3]:
        m = re.match(r"\d+", part)
        if not m:
            break
        digits.append(int(m.group()))
    return tuple(digits)


def _arguments_key() -> str:
    """`arguments` si le dbt installé la comprend, sinon rien.

    Version introuvable : on écrit la syntaxe récente. C'est le cas d'un
    environnement où dbt n'est pas installé du tout — il n'y a alors pas de
    `dbt parse` pour s'en plaindre, et c'est la forme qui a l'avenir pour elle.
    """
    installed = dbt_version()
    return "arguments" if not installed or installed >= ARGUMENTS_SINCE else ""


def _put_arguments(body: dict, arguments: dict) -> None:
    key = _arguments_key()
    if key:
        body[key] = arguments
    else:
        body.update(arguments)


def test_name_of(entry: Any) -> str:
    """Le nom du test porté par une entrée YAML, courte ou longue."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for name in entry:
            return str(name)
    return ""


def _previous_body(previous: Any) -> dict | None:
    """Le corps YAML d'un test déjà écrit, tel quel — commentaires compris.

    On rend l'objet ruamel d'origine plutôt qu'une copie : le modifier sur
    place garde le style et les commentaires du fichier de l'utilisateur.
    """
    if not isinstance(previous, dict):
        return None
    for body in previous.values():
        return body if isinstance(body, dict) else None
    return None


def _previous_args(previous: Any) -> dict:
    """Les arguments d'un test déjà écrit, quelle que soit sa syntaxe."""
    body = _previous_body(previous)
    if not body:
        return {}
    args = body.get("arguments")
    if isinstance(args, dict):
        return dict(args)
    return {k: v for k, v in body.items() if k != "config"}


def test_arguments(spec: dict, column: str = "", previous_to: str = "") -> dict | None:
    """Les arguments dbt d'un test de l'interface, ou None s'il n'en prend pas.

    C'est ici qu'un test incomplet est refusé, plutôt qu'écrit :
    `accepted_values` sans valeur produit `where value not in ()`, que dbt ne
    compile pas — l'erreur apparaîtrait au `dbt build`, loin de l'écran où on
    l'a saisie. `validate_columns` appelle donc cette fonction avant toute
    écriture, pour qu'un refus n'ait rien laissé à moitié écrit.

    `previous_to` est la cible de relation **telle qu'elle est dans le
    fichier**. C'est elle qu'on reconduit quand l'interface n'a pas choisi de
    modèle : conserver une expression est un droit qui vient du disque, pas une
    façon d'écrire du Jinja depuis une requête.
    """
    name = spec.get("name")
    where = f" sur la colonne « {column} »" if column else ""

    if name == "accepted_values":
        values = [v for v in (spec.get("values") or []) if str(v).strip() != ""]
        if not values:
            raise FileError(
                f"Le test « valeurs autorisées »{where} n'a aucune valeur. "
                f"Listez les valeurs acceptées, ou retirez le test."
            )
        return {"values": values}

    if name == "relationships":
        # `to` est un nom de modèle, qu'on enveloppe dans un `ref()`. Mais une
        # relation peut aussi pointer vers une source, un paquet ou une version
        # — des expressions que l'interface ne sait pas éditer. `parse_column_tests`
        # les rend telles quelles dans `to_expr`, et on les réécrit à
        # l'identique : les envelopper dans un second `ref()` produisait
        # `ref('source('raw', 'customers')')`, que dbt ne compile plus.
        target = str(spec.get("to") or "").strip()
        if target:
            if not MODEL_NAME_RE.match(target):
                raise FileError(
                    f"Le test « relation »{where} pointe vers « {target} », qui "
                    f"n'est pas un nom de modèle dbt. Lettres, chiffres et "
                    f"tirets bas, en commençant par une lettre ou un tiret bas."
                )
            expr = f"ref('{target}')"
        else:
            expr = previous_to.strip()
            if not expr:
                raise FileError(
                    f"Le test « relation »{where} n'indique pas vers quoi pointer."
                )
        return {"to": expr, "field": spec.get("field") or "id"}

    return None


def validate_columns(columns: list[dict] | None) -> None:
    """Vérifie tout ce qui peut être refusé, sans rien écrire.

    Appelé avant la première écriture : une sauvegarde qui échoue ne doit pas
    laisser derrière elle un SQL neuf à côté d'un YAML ancien.
    """
    for col in columns or []:
        for spec in col.get("tests") or []:
            # La validation ne voit pas le fichier : elle croit l'expression
            # que la requête dit vouloir conserver. C'est l'écriture qui
            # vérifie qu'elle y était — et elle a la restauration derrière
            # elle si ce n'était pas le cas.
            test_arguments(
                spec,
                str(col.get("name") or ""),
                previous_to=str(spec.get("to_expr") or ""),
            )


def _test_entry(spec: dict, key: str, column: str = "", previous: Any = None) -> Any:
    """Traduit une spec de test de l'interface en YAML dbt.

    `previous` est l'entrée déjà présente dans le fichier pour ce même test.
    L'interface n'édite qu'une poignée de propriétés : tout le reste — les
    arguments d'un test personnalisé, un `where`, un `limit`, un `meta` — doit
    survivre à l'aller-retour, sinon documenter une colonne dégrade ses tests.
    """
    name = spec.get("name")
    severity = str(spec.get("severity") or "error").lower()

    same_test = test_name_of(previous) == name
    body = _previous_body(previous) if same_test else None
    # La cible d'une relation qu'on conserve se lit dans le fichier, jamais
    # dans la requête : sans ça, `to_expr` rouvrait le trou que `to` vient de
    # fermer, et laissait écrire du Jinja arbitraire.
    precedent_to = str((_previous_args(previous) if same_test else {}).get("to") or "")
    arguments = test_arguments(spec, column, previous_to=precedent_to)

    if body is None:
        # Forme courte quand il n'y a rien à préciser : `tests: [unique, not_null]`.
        if arguments is None and severity == "error":
            return name
        fresh: dict = {}
        if arguments is not None:
            _put_arguments(fresh, arguments)
        if severity != "error":
            fresh["config"] = {"severity": severity}
        return {name: fresh}

    # On repart du YAML existant et on n'y touche que ce que l'interface édite.
    # `arguments:` est la syntaxe dbt récente ; un fichier qui met encore ses
    # arguments au premier niveau garde sa forme plutôt que d'être converti.
    if arguments is not None:
        old = body.get("arguments")
        if isinstance(old, dict):
            old.update(arguments)
        elif "arguments" in body or not any(k != "config" for k in body):
            # `arguments:` écrit à la main sous une forme qu'on ne sait pas lire
            # (une liste, un scalaire), ou entrée sans argument du tout : il n'y
            # a rien à y préserver, et on repart de la forme que le dbt installé
            # comprend.
            body.pop("arguments", None)
            _put_arguments(body, arguments)
        else:
            body.update(arguments)

    cfg = body.get("config")
    if severity != "error":
        if not isinstance(cfg, dict):
            cfg = {}
            body["config"] = cfg
        cfg["severity"] = severity
    elif isinstance(cfg, dict):
        cfg.pop("severity", None)
        if not cfg:
            body.pop("config", None)

    return name if not body else {name: body}


def parse_column_tests(column: dict) -> list[dict]:
    """YAML dbt → forme normalisée pour l'interface."""
    raw = column.get("data_tests")
    if raw is None:
        raw = column.get("tests")
    out: list[dict] = []
    for entry in raw or []:
        if isinstance(entry, str):
            out.append({"name": entry, "severity": "error"})
            continue
        if not isinstance(entry, dict):
            continue
        for name, body in entry.items():
            body = dict(body or {})
            args = dict(body.get("arguments") or {})
            if not args:  # ancienne syntaxe : arguments au premier niveau
                args = {k: v for k, v in body.items() if k != "config"}
            severity = str((body.get("config") or {}).get("severity", "error")).lower()
            item: dict = {"name": str(name), "severity": severity}
            if name == "accepted_values":
                item["values"] = [str(v) for v in (args.get("values") or [])]
            elif name == "relationships":
                # `to` est du Jinja. `ref('modele')` est la seule forme que
                # l'interface sait éditer ; `source(...)`, `ref('paquet', 'x')`
                # ou une macro sont rendues à part, telles quelles, pour être
                # réécrites à l'identique. Les faire passer par `to` les
                # enveloppait dans un second `ref()`, et le test cassait.
                to = str(args.get("to") or "")
                m = REF_RE.match(to)
                item["to"] = m.group(1) if m else ""
                if not m and to:
                    item["to_expr"] = to
                item["field"] = str(args.get("field") or "id")
            out.append(item)
    return out


# Les trois familles documentables de dbt, et où les trouver dans un YAML.
SECTIONS = {"model": "models", "seed": "seeds", "snapshot": "snapshots"}

# Les unités que dbt accepte pour un seuil de fraîcheur.
FRESHNESS_PERIODS = ("minute", "hour", "day")

# Ces adaptateurs savent lire la date de dernière écriture dans les métadonnées
# de l'entrepôt : `loaded_at_field` y est facultatif. Ailleurs, sans lui, dbt
# saute la source — la fraîcheur n'est alors pas bonne, elle n'est pas mesurée.
FRESHNESS_FROM_METADATA = ("snowflake", "bigquery", "redshift", "spark", "databricks")


# Ce qu'une entrée de YAML ne déclare pas du tout — à distinguer de ce qu'elle
# déclare nul, qui est la façon dbt de désactiver un héritage.
_ABSENT = object()


def _freshness_slots(entry: dict, key: str) -> list[dict]:
    """Les emplacements où dbt lit cette propriété, du plus fort au plus faible.

    dbt en accepte deux — au premier niveau d'une entrée, et sous son `config:`,
    où ces propriétés vivent depuis dbt 1.10 — mais pas avec la même préséance :
    `config:` l'emporte pour `freshness`, le premier niveau l'emporte pour
    `loaded_at_field`. Écrire toujours au premier niveau laissait la
    modification sans effet sur une source qui avait choisi l'autre emplacement.
    """
    cfg = entry.get("config")
    cfg = cfg if isinstance(cfg, dict) else None
    order = [cfg, entry] if key == "freshness" else [entry, cfg]
    return [d for d in order if d is not None]


def _declared_freshness(entry: dict | None, key: str) -> Any:
    """Ce que cette entrée déclare pour cette propriété, ou `_ABSENT`."""
    if not isinstance(entry, dict):
        return _ABSENT
    for or_ in _freshness_slots(entry, key):
        if key in or_:
            return or_[key]
    return _ABSENT


def _write_slot(entry: dict, key: str) -> dict:
    """Où écrire : là où l'entrée porte déjà la propriété, sinon au premier niveau."""
    for or_ in _freshness_slots(entry, key):
        if key in or_:
            return or_
    return entry


def _forget_freshness(entry: dict, key: str) -> None:
    """Retire la propriété de tous ses emplacements : un reste ferait loi."""
    for or_ in _freshness_slots(entry, key):
        or_.pop(key, None)


def _normalize_field(value: Any) -> str | None:
    """La colonne de chargement telle qu'on la compare : vide vaut absente."""
    return str(value).strip() or None if isinstance(value, str) else None


def _normalize_thresholds(value: Any) -> dict | None:
    """Les deux seuils d'un bloc `freshness`, réduits à ce que l'atelier règle.

    Sert à comparer ce que le formulaire renvoie à ce qui est déjà en vigueur :
    sans cette mise à plat, un bloc dbt qui porte aussi un `filter` ne serait
    jamais reconnu comme inchangé.
    """
    if not isinstance(value, dict):
        return None
    out: dict[str, dict] = {}
    for key in ("warn_after", "error_after"):
        t = value.get(key)
        if isinstance(t, dict) and t.get("count"):
            out[key] = {
                "count": int(t["count"]),
                "period": str(t.get("period") or "hour"),
            }
    return out or None


def _set_freshness(
    entry: dict,
    group: dict | None,
    key: str,
    wanted: Any,
    active: Any,
    normalize: Callable[[Any], Any],
) -> None:
    """Écrit une propriété de fraîcheur là où dbt la lira — ou la désactive.

    Trois valeurs se croisent : ce que le formulaire renvoie (`wanted`), ce que
    dbt applique aujourd'hui (`active`, héritages compris), et ce que l'entrée
    déclare pour elle-même. Ce croisement est ce qui manquait : la lecture ne
    voyait que la déclaration propre de la table, l'écriture ne touchait qu'elle,
    et vider un seuil hérité du groupe `sources:` répondait « enregistré » sans
    rien désactiver.

    Un héritage ne se retire pas, il se recouvre : dbt ne l'annule que sur une
    valeur nulle explicite. On ne l'écrit que lorsqu'il le faut — retirer la
    déclaration de la table suffit quand elle était toute l'histoire, et le YAML
    reste alors propre.
    """
    clean = _declared_freshness(entry, key)
    inherited = _declared_freshness(group, key)

    if wanted is not None:
        if active is not _ABSENT and wanted == active:
            return  # déjà en vigueur : ne pas matérialiser un héritage pour rien
        slot = _write_slot(entry, key)
        block = slot.get(key)
        # Mise à jour sur place : `filter`, les commentaires et l'ordre des clés
        # appartiennent au projet, pas à l'atelier.
        if key == "freshness" and isinstance(block, dict) and isinstance(wanted, dict):
            for threshold in ("warn_after", "error_after"):
                block.pop(threshold, None)
            block.update(wanted)
        else:
            slot[key] = wanted
        return

    if active is _ABSENT or active is None:
        _forget_freshness(entry, key)  # rien n'était en vigueur : rien à annuler
        return

    _forget_freshness(entry, key)
    # Retirer la déclaration de la table suffit quand elle était toute
    # l'histoire. Sinon ce qui reste en vigueur vient d'ailleurs — du groupe
    # `sources:`, d'un `config:` plus haut — et il faut le dire à dbt : `null`
    # est le seul mot qu'il entende pour ça.
    comes_from_group = inherited is not _ABSENT and normalize(inherited) is not None
    clean_explained = clean is not _ABSENT and normalize(clean) == active
    if comes_from_group or not clean_explained:
        _write_slot(entry, key)[key] = None


def _write_freshness(
    settings: Settings,
    entry: dict,
    cfg: dict,
    *,
    group: dict | None = None,
    resolved: dict | None = None,
) -> None:
    """`loaded_at_field` et le bloc `freshness` d'une table de source.

    `loaded_at_field` n'est pas forcément un nom de colonne : dbt y accepte une
    expression SQL, et il en faut souvent une — il exige un horodatage, là où
    beaucoup de tables n'ont qu'une date. `cast(order_date as timestamp)` est
    la réponse, et l'atelier doit pouvoir l'écrire.

    `resolved` est ce que dbt applique en ce moment à cette table, héritages et
    `config:` compris — le serveur le tient du manifeste. Sans lui (appel
    direct, hors atelier), on s'en tient à ce que l'entrée déclare.
    """
    field = str(cfg.get("loaded_at_field") or "").strip()
    if field:
        try:
            rcp.check_expr(field, "colonne de chargement")
        except rcp.RecipeError as exc:
            raise FileError(str(exc)) from None

    thresholds: dict[str, dict] = {}
    for key in ("warn_after", "error_after"):
        raw_text = cfg.get(key)
        if not raw_text:
            continue
        try:
            count = int(raw_text.get("count"))
        except (TypeError, ValueError, AttributeError):
            raise FileError(
                f"Seuil de fraîcheur « {key} » : il faut un nombre entier."
            ) from None
        if count <= 0:
            raise FileError(
                f"Seuil de fraîcheur « {key} » : il faut un nombre positif."
            )
        period = str(raw_text.get("period") or "hour").strip().lower()
        if period not in FRESHNESS_PERIODS:
            raise FileError(
                f"Unité de fraîcheur inconnue : « {period} ». dbt en accepte "
                f"trois : {', '.join(FRESHNESS_PERIODS)}."
            )
        thresholds[key] = {"count": count, "period": period}

    adapter = str(settings.target_config().get("type") or "").lower()
    if thresholds and not field and adapter not in FRESHNESS_FROM_METADATA:
        raise FileError(
            f"Un seuil de fraîcheur sans colonne de chargement ne mesure rien "
            f"sur « {adapter or 'cet entrepôt'} » : dbt saute la source, et "
            f"rien ne le dit dans le Flow. Choisissez la colonne qui porte la "
            f"date d'arrivée des lignes."
        )

    _set_freshness(
        entry,
        group,
        "loaded_at_field",
        field or None,
        (
            (str(resolved.get("loaded_at_field") or "") or None)
            if resolved is not None
            else _ABSENT
        ),
        _normalize_field,
    )
    _set_freshness(
        entry,
        group,
        "freshness",
        thresholds or None,
        _normalize_thresholds(resolved) if resolved is not None else _ABSENT,
        _normalize_thresholds,
    )


def _section(data: dict, kind: str) -> list:
    """La liste du YAML qui accueille ce type de ressource, créée au besoin."""
    key = SECTIONS.get(kind)
    if key is None:
        raise FileError(f"Type de ressource non documentable : {kind}")
    if not data.get(key):
        data[key] = []
    return data[key]


def _find_or_add(entries: list, name: str) -> dict:
    for e in entries:
        if isinstance(e, dict) and e.get("name") == name:
            return e
    entry = {"name": name}
    entries.append(entry)
    return entry


def _source_group(data: dict, source_name: str) -> dict:
    """Le groupe `sources:` qui déclare cette source.

    Il compte autant que la table : `freshness` et `loaded_at_field` s'y
    déclarent pour toutes ses tables, et une table qui n'en dit rien en hérite.
    """
    for group in data.get("sources") or []:
        if isinstance(group, dict) and group.get("name") == source_name:
            return group
    raise FileError(f"La source « {source_name} » n'est pas déclarée dans ce fichier.")


def _source_table(data: dict, source_name: str, table: str) -> dict:
    """L'entrée d'une table de source, dans le groupe qui la déclare."""
    group = _source_group(data, source_name)
    declared = group.get("tables")
    # Même règle que `datasets.declare_source` : on complète une liste absente,
    # on refuse une autre forme. `_find_or_add` y ferait un `.append()`.
    if declared is None:
        group["tables"] = []
    elif not isinstance(declared, list):
        raise FileError(
            f"Le groupe de sources « {source_name} » déclare un « tables: » "
            f"qui n'est pas une liste. Corrigez le fichier : l'atelier ne "
            f"réécrit pas ce qu'il ne sait pas lire."
        )
    return _find_or_add(group["tables"], table)


def upsert_doc(
    settings: Settings,
    schema_relative_path: str,
    name: str,
    *,
    kind: str = "model",
    source_name: str = "",
    description: str | None = None,
    columns: list[dict] | None = None,
    tags: list[str] | None = None,
    freshness: dict | None = None,
    resolved_freshness: dict | None = None,
) -> str:
    """Documente une ressource dbt : description, tags, colonnes, tests.

    Marche pour un modèle, un seed, un snapshot et une table de source — c'est
    le même YAML, seule la section change. `freshness` ne vaut que pour une
    source : c'est la seule famille que `dbt source freshness` regarde.

    `resolved_freshness` est ce que dbt applique aujourd'hui à cette table,
    héritages compris : c'est ce qui permet de distinguer « inchangé » de
    « mis à zéro », et donc de désactiver un seuil qui vient du groupe.
    """
    path = safe_path(settings, schema_relative_path)
    data = _load_yaml(path)
    key = _tests_key(data)

    if kind == "source":
        entry = _source_table(data, source_name, name)
    else:
        entry = _find_or_add(_section(data, kind), name)

    if description is not None:
        if description:
            entry["description"] = DQ(description)
        else:
            entry.pop("description", None)

    if freshness is not None:
        if kind != "source":
            raise FileError(
                "La fraîcheur ne se règle que sur une table de source : c'est "
                "la seule chose que `dbt source freshness` regarde."
            )
        _write_freshness(
            settings,
            entry,
            freshness,
            group=_source_group(data, source_name),
            resolved=resolved_freshness,
        )

    if tags is not None:
        clean = [t.strip() for t in tags if str(t).strip()]
        if kind == "source":
            # Une source porte ses tags au premier niveau.
            if clean:
                entry["tags"] = clean
            else:
                entry.pop("tags", None)
        else:
            # Un modèle ou un seed les porte dans son bloc `config`. Mais dbt
            # accepte aussi `tags:` au premier niveau de l'entrée, et des
            # projets existants l'écrivent ainsi — la *lecture* le reconnaît
            # d'ailleurs déjà. N'écrire que dans `config` laissait l'ancienne
            # forme en place : le tag retiré revenait au rafraîchissement
            # suivant, et avec les deux formes présentes la lecture pouvait
            # montrer autre chose que ce qu'on venait d'écrire. On normalise
            # donc les deux emplacements d'un seul geste.
            cfg = entry.get("config")
            entry.pop("tags", None)
            if clean:
                if not isinstance(cfg, dict):
                    cfg = {}
                    entry["config"] = cfg
                cfg["tags"] = clean
            elif isinstance(cfg, dict):
                cfg.pop("tags", None)
                if not cfg:
                    entry.pop("config", None)

    for col in columns or []:
        # `col_name` et non `name` : `name` est la ressource qu'on documente,
        # et l'écraser ici en ferait, au premier code qui le relirait après la
        # boucle, le nom de la dernière colonne traitée. Rien ne le relit
        # aujourd'hui — c'est justement pour ça qu'il fallait le nommer avant
        # que ce soit un bug.
        col_name = col.get("name")
        if not col_name:
            continue
        cols = entry.setdefault("columns", [])
        target = None
        for c in cols:
            if c.get("name") == col_name:
                target = c
                break
        if target is None:
            target = {"name": col_name}
            cols.append(target)
        if "description" in col:
            if col["description"]:
                target["description"] = DQ(col["description"])
            else:
                target.pop("description", None)

        if "tests" in col:
            # Les entrées déjà en place servent de base : on les apparie par
            # nom de test, et celles qui restent disparaissent — l'interface
            # les a retirées.
            existing = list(target.get("data_tests") or target.get("tests") or [])
            target.pop("tests", None)
            target.pop("data_tests", None)
            specs = col.get("tests") or []
            if specs:
                written = []
                for spec in specs:
                    previous = None
                    # `old` et pas `entry` : `entry` désigne la ressource
                    # qu'on documente, et l'écraser ici perdait les colonnes
                    # suivantes — ou plantait sur un test en forme courte.
                    for i, old in enumerate(existing):
                        if test_name_of(old) == spec.get("name"):
                            previous = existing.pop(i)
                            break
                    written.append(_test_entry(spec, key, col_name, previous))
                target[key] = written

        # Une colonne sans description ni test n'a rien à faire dans le YAML.
        if list(target.keys()) == ["name"]:
            cols.remove(target)

    if entry.get("columns") == []:
        entry.pop("columns")

    _dump_yaml(path, data)
    return schema_relative_path


def read_doc(
    settings: Settings,
    schema_relative_path: str,
    name: str,
    *,
    kind: str = "model",
    source_name: str = "",
) -> dict:
    """L'entrée YAML d'une ressource, telle qu'elle est écrite sur le disque."""
    try:
        path = safe_path(settings, schema_relative_path)
    except FileError:
        return {}
    if not path.exists():
        return {}
    try:
        data = _yaml.load(path.read_text()) or {}
    except Exception:  # noqa: BLE001
        return {}
    # Un YAML valide dont la racine est une séquence n'a pas de `.get` : la
    # lecture tombait en `AttributeError` sur un fichier que dbt lui-même
    # refuserait, et le refus arrivait sous forme de 500.
    if not isinstance(data, dict):
        return {}

    if kind == "source":
        for group in data.get("sources") or []:
            if isinstance(group, dict) and group.get("name") == source_name:
                for t in group.get("tables") or []:
                    if isinstance(t, dict) and t.get("name") == name:
                        return dict(t)
        return {}

    for entry in data.get(SECTIONS.get(kind, "models")) or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            out = dict(entry)
            cfg = out.get("config")
            # Les deux emplacements réunis, `config` d'abord : c'est celui que
            # l'atelier écrit. Avec les deux présents, ne montrer que l'un des
            # deux laissait croire qu'un tag avait disparu.
            tags: list = []
            for source in (
                cfg.get("tags") if isinstance(cfg, dict) else None,
                out.get("tags"),
            ):
                if isinstance(source, str):
                    source = [source]
                for t in source or []:
                    if t not in tags:
                        tags.append(t)
            if tags:
                out["tags"] = tags
            return out
    return {}


def doc_digest(
    settings: Settings,
    schema_relative_path: str,
    name: str,
    *,
    kind: str = "model",
    source_name: str = "",
) -> str | None:
    """Empreinte de la seule entrée d'une ressource, ou None si elle n'existe pas.

    L'empreinte du fichier entier serait trop grossière : documenter le modèle
    voisin ferait croire que celui-ci a changé, et refuserait une sauvegarde
    qui n'entre en conflit avec rien.
    """
    entry = read_doc(
        settings, schema_relative_path, name, kind=kind, source_name=source_name
    )
    if not entry:
        return None
    buf = io.StringIO()
    _yaml.dump(entry, buf)
    return hashlib.sha256(buf.getvalue().encode()).hexdigest()[:16]


# --------------------------------------------- YAML générique (sources, seeds)


def load_yaml(settings: Settings, relative: str) -> dict:
    """Charge un YAML du projet en préservant son style, ou {} s'il n'existe pas."""
    path = safe_path(settings, relative)
    if not path.exists():
        return {}
    try:
        return _yaml.load(path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001 — ruamel a sa propre famille
        raise _yaml_error(path, exc) from None


def dump_yaml(settings: Settings, relative: str, data: Any) -> str:
    _dump_yaml(safe_path(settings, relative), data)
    return relative


def find_yaml_with(
    settings: Settings, key: str, roots: list[Path]
) -> list[tuple[str, dict]]:
    """Tous les YAML du projet qui contiennent une clé donnée (`sources`, `seeds`…)."""
    out: list[tuple[str, dict]] = []
    root = settings.project_dir.resolve()
    for base in roots:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.yml")) + sorted(base.rglob("*.yaml")):
            try:
                data = _yaml.load(path.read_text()) or {}
            except Exception:  # noqa: BLE001 — un YAML cassé ne doit pas tout bloquer
                continue
            # `key in data` et non `data.get(key)` : un fichier avec
            # `sources: []` déclare bien l'endroit où ranger les sources.
            if isinstance(data, dict) and key in data:
                out.append((str(path.relative_to(root)), data))
    return out


def remove_doc(
    settings: Settings,
    schema_relative_path: str,
    name: str,
    *,
    kind: str = "model",
) -> bool:
    """Retire une ressource d'un schema.yml. Vrai si une entrée a été retirée.

    On supprime l'entrée sur place, sans reconstruire la liste, pour que les
    commentaires et l'ordre du reste du fichier survivent. Un schema.yml qui ne
    documenterait plus rien est effacé : il ne dirait plus que `version: 2`.
    """
    path = safe_path(settings, schema_relative_path)
    if not path.exists():
        return False
    key = SECTIONS.get(kind)
    if key is None:
        raise FileError(f"Type de ressource non documentable : {kind}")

    data = _load_yaml(path)
    entries = data.get(key) or []
    found = next(
        (
            i
            for i, e in enumerate(entries)
            if isinstance(e, dict) and e.get("name") == name
        ),
        None,
    )
    if found is None:
        return False

    del entries[found]
    if not entries:
        data.pop(key, None)

    if set(data) <= {"version"}:
        path.unlink()
    else:
        _dump_yaml(path, data)
    return True


def rename_doc(
    settings: Settings,
    schema_relative_path: str,
    old: str,
    new: str,
    *,
    kind: str = "model",
) -> bool:
    """Renomme une ressource dans un schema.yml. Vrai si une entrée a changé.

    On change la clé `name` sur place plutôt que de retirer l'entrée pour la
    réécrire : sa position dans le fichier, les commentaires autour, et les
    tests que l'interface ne sait pas éditer survivent tous au renommage.
    """
    path = safe_path(settings, schema_relative_path)
    if not path.exists():
        return False
    key = SECTIONS.get(kind)
    if key is None:
        raise FileError(f"Type de ressource non documentable : {kind}")

    data = _load_yaml(path)
    entry = next(
        (
            e
            for e in (data.get(key) or [])
            if isinstance(e, dict) and e.get("name") == old
        ),
        None,
    )
    if entry is None:
        return False
    entry["name"] = new
    _dump_yaml(path, data)
    return True


# Ce que dbt lit dans un projet, et donc ce qu'on relit pour y suivre un
# renommage. Le YAML compte autant que le SQL : un test de relation y nomme sa
# cible dans un `ref()`, exactement comme un modèle. Et `.py` autant que les
# deux autres : un modèle Python lit ses dépendances par `dbt.ref("…")`, et
# l'exclure laissait ces appels désigner un modèle qui n'existe plus — le
# renommage répondait succès, et c'est le build suivant qui tombait.
LANGUAGE_BY_SUFFIX = {
    ".sql": "jinja",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".py": "python",
}
CODE_SUFFIXES = tuple(LANGUAGE_BY_SUFFIX)


def language_of(path: Path) -> str:
    return LANGUAGE_BY_SUFFIX.get(path.suffix, "jinja")


def code_files_naming(settings: Settings, name: str) -> list[Path]:
    """Les fichiers du projet qui nomment ce modèle dans un `ref()`.

    Séparé de la réécriture parce qu'un renommage doit connaître *avant*
    d'écrire tous les fichiers qu'il touchera : c'est ce que `all_or_nothing`
    sauvegarde pour pouvoir les rendre s'il échoue en route.
    """
    root = settings.project_dir.resolve()
    out: list[Path] = []
    # Le `dbt_project.yml` de la racine n'est dans aucun dossier de code, et
    # ses `on-run-start` / `on-run-end` portent pourtant du Jinja qui peut
    # nommer le modèle. L'ignorer laissait un hook cassé derrière un
    # renommage annoncé complet.
    project_yml = settings.project_dir / "dbt_project.yml"
    if project_yml.is_file():
        try:
            project_yml_text = project_yml.read_text()
            if (
                rcp.rename_ref_in_sql(
                    project_yml_text,
                    name,
                    f"{name}__pliq_sonde",
                    settings.project_name,
                    language="yaml",
                )
                != project_yml_text
            ):
                out.append(project_yml)
        except (OSError, UnicodeDecodeError):
            pass
    for rel in settings.code_dirs():
        base = settings.project_dir / rel
        if not base.is_dir():
            continue
        # Un `model-paths: ["../partage"]` fait sortir de l'arbre avant même
        # qu'on ait lu un fichier. dbt l'accepte ; l'atelier, qui n'écrit que
        # dans le projet, doit le dire au lieu d'écrire là quand même.
        if not under_root(base.resolve(), root):
            raise FileError(
                f"« {rel} » est configuré hors du projet ({base.resolve()}). "
                f"L'atelier n'écrit pas hors de l'arbre du projet : reprenez "
                f"les ref() de ces fichiers à la main."
            )
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in CODE_SUFFIXES:
                continue
            try:
                text = path.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            # La détection emploie exactement la règle de la réécriture : lister
            # un fichier que le renommage ne touchera pas gonflerait l'aperçu
            # d'un « fichiers mis à jour » qui ne l'est pas.
            probe = rcp.rename_ref_in_sql(
                text,
                name,
                f"{name}__pliq_sonde",
                settings.project_name,
                language=language_of(path),
            )
            if probe == text:
                continue
            # `path.is_file()` suit les liens, et la réécriture aussi : écrire
            # dans `models/consumer.sql` quand c'est un lien vers
            # `../partage.sql` modifie le fichier partagé. Les contrôles posés
            # sur le modèle renommé ne protégeaient pas ceux qui le nomment.
            # On refuse le renommage entier plutôt que de sauter le fichier :
            # sauté, il continuerait de nommer l'ancien modèle, et dbt ne
            # parserait plus le projet.
            target = path.resolve()
            if not under_root(target, root):
                raise FileError(
                    f"« {path.relative_to(settings.project_dir)} » mène hors du "
                    f"projet ({target}) et nomme « {name} ». L'atelier n'écrit "
                    f"pas hors de l'arbre du projet : reprenez ce fichier à la "
                    f"main, ou remplacez le lien par une copie."
                )
            out.append(path)
    return out


def rename_refs(settings: Settings, old: str, new: str, paths: list[Path]) -> list[str]:
    """Réécrit `ref('old')` en `ref('new')` dans les fichiers donnés.

    Rend les chemins effectivement modifiés. La réécriture est textuelle et ne
    touche qu'au `ref()` : un YAML garde son style, un SQL écrit à la main garde
    sa mise en forme, et rien d'autre ne bouge dans le fichier.
    """
    if old == new:
        return []
    package = settings.project_name
    touches: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        before = path.read_text()
        after = rcp.rename_ref_in_sql(
            before, old, new, package, language=language_of(path)
        )
        if after != before:
            write_text_atomically(path, after)
            touches.append(str(path.relative_to(settings.project_dir)))
    return touches


def configs_naming(settings: Settings, name: str) -> list[str]:
    """Les configurations du `dbt_project.yml` attachées à ce nom de modèle.

    `models: <projet>: <nom>: …` n'est pas une référence : c'est une clé de
    configuration. La réécrire à l'aveugle déplacerait des réglages sans
    qu'on sache s'ils visaient bien ce modèle ; la taire fait perdre au modèle
    renommé sa matérialisation, ses tags ou son schéma. On la *signale* donc,
    avec son chemin dans le fichier, pour que la décision reste à
    l'utilisateur.
    """
    data = settings.project_yml
    found: list[str] = []

    def descend(node, path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == name:
                found.append(":".join([*path, str(key)]))
            if isinstance(value, dict) and not str(key).startswith("+"):
                descend(value, [*path, str(key)])

    for section in ("models", "seeds", "snapshots"):
        descend(data.get(section), [section])
    return found

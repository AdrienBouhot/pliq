"""Pilotage de dbt Core via son API programmatique (dbtRunner).

On n'imite pas dbt et on ne lance pas de sous-processus : on l'exécute en
mémoire, ce qui donne le manifest, les résultats structurés et les événements
de log au fil de l'eau.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from dbt.adapters.factory import reset_adapters
from dbt.cli.main import dbtRunner

from .config import Settings

logger = logging.getLogger("pliq")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Événements dbt qui portent l'état d'un nœud.
NODE_EVENTS = {
    "NodeStart",
    "NodeFinished",
    "LogStartLine",
    "LogModelResult",
    "LogTestResult",
    "LogSeedResult",
    "LogSnapshotResult",
    "LogFreshnessResult",
}

# Bruit de fond qu'on ne remonte pas dans le journal de l'interface.
QUIET_EVENTS = {
    "SendingEvent",
    "MainReportArgs",
    "AdapterEventDebug",
    "NewConnection",
    "ConnectionUsed",
    "ConnectionReused",
    "ConnectionLeftOpenInCleanup",
    "ConnectionClosedInCleanup",
    "SQLQuery",
    "SQLQueryStatus",
    "SQLCommit",
    "CacheAction",
    "CacheDumpGraph",
    "RunResultWarning",
}


@dataclass
class NodeState:
    status: str = "idle"  # idle | running | success | error | warn | skipped
    execution_time: float | None = None
    message: str | None = None
    rows_affected: int | None = None


@dataclass
class RunState:
    """État de l'exécution en cours ou de la dernière terminée."""

    run_id: int = 0
    command: str = ""
    select: str = ""
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    success: bool | None = None
    lines: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "select": self.select,
            "running": self.running,
            "success": self.success,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": (
                (self.finished_at or time.time()) - self.started_at
                if self.started_at
                else None
            ),
        }


# Une commande de l'atelier n'est pas toujours un seul mot pour dbt.
COMMAND_ARGS = {"freshness": ["source", "freshness"]}

# Ce que dbt écrit dans `sources.json`, traduit dans le vocabulaire du Flow.
FRESHNESS_STATUS = {
    "pass": "success",
    "warn": "warn",
    "error": "error",
    "runtime error": "error",
}


class SelectionError(ValueError):
    """Un champ de sélection contient autre chose qu'un sélecteur dbt."""


def _selectors_only(raw_text: str, what: str) -> None:
    """Refuse ce qui, découpé sur les espaces, deviendrait une option dbt.

    `select` et `exclude` sont poussés morceau par morceau sur la ligne de
    commande. Un morceau qui commence par `-` n'est plus un sélecteur : c'est
    une option, et dbt l'accepte comme telle. `m --target prod` tapé dans le
    champ du bandeau construisait donc un build sur la production — les
    arguments de session sont ajoutés après, mais ils ne portent `--target` que
    si la session en a un. Lancé sans `--target`, l'atelier n'avait rien à
    opposer, répondait succès, et l'écran du Flow n'affiche aucune cible.

    On ferme le champ plutôt que de filtrer les options une à une : ce champ
    prend des sélecteurs, et rien d'autre n'y a sa place.
    """
    for chunk in (raw_text or "").split():
        if chunk.startswith("-"):
            raise SelectionError(
                f"« {chunk} » n'est pas un sélecteur dbt : ce champ prend des "
                f"noms de nœuds et des méthodes de sélection "
                f"(`tag:finance`, `stg_orders+`, `path:models/marts`), pas des "
                f"options en ligne de commande. Retirez-le du {what}."
            )


class ShowError(RuntimeError):
    """Une lecture via `dbt show` a échoué."""


class RunInProgress(RuntimeError):
    """Une invocation dbt a été demandée pendant qu'un run occupe déjà dbt.

    dbt n'accepte qu'une invocation à la fois dans un process : `invoke` tient
    donc un verrou pour toute la durée de la commande, `dbt build` compris.
    Une lecture partie pendant un build ne se trompait pas — elle *attendait*,
    parfois plusieurs minutes, et le pire est qu'elle attendait en tenant le
    verrou de projet du serveur : toutes les autres routes faisaient la queue
    derrière elle. Quelques clics pendant un build, et l'atelier ne répondait
    plus à rien, sans qu'un seul message ne l'explique.

    Attendre était le mauvais choix : ce qui manque à l'écran, c'est la
    nouvelle qu'un build occupe la place. On refuse donc, tout de suite et au
    seul endroit qui bloquait vraiment — les routes qui ne passent pas par dbt
    continuent de répondre.
    """


class DbtService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._manifest = None
        self._manifest_error: str | None = None
        self.node_state: dict[str, NodeState] = {}
        # La fraîcheur des sources, lue dans `target/sources.json`. Elle vit à
        # part de `node_state` : une source n'est pas « construite », elle est
        # récente ou elle ne l'est pas, et c'est une autre commande qui le dit.
        self.freshness: dict[str, dict] = {}
        self.run = RunState()
        self._run_counter = 0
        # Ce que le manifeste vaut « en ce moment ». Chaque parse réussi
        # l'incrémente : c'est ce qui permet à l'entrepôt de garder en cache
        # les colonnes d'une entrée sans risquer de les servir périmées après
        # un enregistrement. Un entier plutôt qu'une date : deux parses de la
        # même milliseconde doivent se distinguer.
        self.generation = 0

    # ------------------------------------------------------------------ dbt

    def _base_args(self) -> list[str]:
        args = [
            "--project-dir",
            str(self.settings.project_dir),
            "--profiles-dir",
            str(self.settings.profiles_dir),
        ]
        # La cible résolue, pas seulement celle qu'on a demandée : sans elle,
        # `DBT_TARGET` dans l'environnement faisait construire dbt sur une cible
        # que l'atelier n'affichait nulle part.
        target = self.settings.command_target()
        if target:
            args += ["--target", target]
        return args

    def invoke(
        self,
        args: list[str],
        on_event: Callable[[dict], None] | None = None,
        manifest=None,
        capture_stdout: bool = False,
        reset: bool = False,
        for_run: bool = False,
    ):
        """Exécute une commande dbt. Sérialisé : une seule à la fois.

        `manifest` évite un reparse quand on en a déjà un à jour (les aperçus
        en enchaînent beaucoup). `capture_stdout` sert à `dbt show`, qui écrit
        sa table sur la sortie standard même avec `--quiet`.

        `reset` relâche la connexion à l'entrepôt en sortant. Un *run* le veut :
        il a pu changer le schéma, et le suivant doit repartir propre. Une
        lecture, non — et c'est pourtant ce qu'on faisait. Chaque `dbt show`
        fermait la connexion qu'il venait d'ouvrir, si bien qu'un aperçu de
        jointure (une lecture de colonnes par entrée, plus la requête) en
        rouvrait trois. Sur DuckDB c'était 0,4 s par lecture ; sur un entrepôt
        distant, c'est une poignée de main d'authentification à chaque frappe.
        La bascule de projet garde la sienne : `release()`, seul endroit où
        lâcher la base est vraiment nécessaire — DuckDB n'accepte qu'un
        écrivain.

        `for_run` est le laissez-passer du thread de run : c'est lui qui
        tient le verrou, il ne doit pas se refuser l'entrée à lui-même.
        """
        # Avant le verrou, et non derrière : derrière, on *attendrait* la fin
        # du build au lieu de le dire. Voir `RunInProgress`.
        #
        # La course apparente — un run qui démarre juste après ce test — n'en
        # est pas une : `start_run` pose `running` avant de lancer son thread,
        # et il n'est appelé que sous le verrou de projet du serveur, celui-là
        # même que tiennent les routes qui lisent. L'une exclut l'autre.
        if self.run.running and not for_run:
            raise RunInProgress(
                "Une exécution dbt est en cours, et dbt n'en accepte qu'une à "
                "la fois : cette lecture attendrait la fin du build. Suivez-la "
                "dans le journal, puis réessayez."
            )

        callbacks = []
        if on_event:
            callbacks.append(lambda e: self._forward(e, on_event))

        with self._lock:
            runner = dbtRunner(manifest=manifest, callbacks=callbacks)
            sink = (
                contextlib.redirect_stdout(io.StringIO())
                if capture_stdout
                else contextlib.nullcontext()
            )
            try:
                with sink:
                    return runner.invoke(args + self._base_args())
            finally:
                if reset:
                    # dbt garde sa connexion à l'entrepôt ouverte entre deux
                    # appels. On la relâche pour que le run suivant reparte
                    # d'un état propre.
                    try:
                        reset_adapters()
                    except Exception:  # noqa: BLE001
                        pass

    def _forward(self, event, on_event: Callable[[dict], None]) -> None:
        try:
            info = event.info
            name = info.name
            if name in QUIET_EVENTS or info.level == "debug":
                return

            payload = {
                "type": "log",
                "event": name,
                "level": info.level,
                "msg": ANSI_RE.sub("", info.msg),
                "ts": time.time(),
            }

            if name in NODE_EVENTS:
                node_info = getattr(event.data, "node_info", None)
                uid = getattr(node_info, "unique_id", "") if node_info else ""
                # Une source n'est jamais construite : `source freshness` émet
                # pourtant des événements de nœud, et sa réussite ou son échec
                # se serait affiché sur la pastille de statut du Flow, à côté
                # de la pastille de fraîcheur qui dit déjà la même chose.
                if uid and not str(uid).startswith("source."):
                    status = (getattr(node_info, "node_status", "") or "").lower()
                    payload["node"] = uid
                    payload["node_status"] = status
                    self._apply_node_status(uid, status, node_info)

            on_event(payload)
        except Exception:  # noqa: BLE001 — un souci de log ne casse pas un run
            pass

    def _apply_node_status(self, uid: str, status: str, node_info) -> None:
        mapped = {
            "started": "running",
            "compiling": "running",
            "executing": "running",
            "success": "success",
            "pass": "success",
            "warn": "warn",
            "error": "error",
            "fail": "error",
            "skipped": "skipped",
            "runtime error": "error",
        }.get(status, status or "idle")

        st = self.node_state.setdefault(uid, NodeState())
        st.status = mapped
        and_ = getattr(node_info, "node_finished_at", None)
        if mapped in ("success", "warn", "error", "skipped"):
            st.execution_time = (
                getattr(node_info, "node_execution_time", None) or st.execution_time
            )
        if and_:
            st.message = None

    # ------------------------------------------------------------- manifest

    def parse(self, force: bool = False, for_run: bool = False):
        """(Re)parse le projet. Retourne le Manifest dbt.

        `for_run` est le laissez-passer du thread de run, qui reparse en
        fin de course : sans lui, il se refuserait l'entrée à lui-même et le
        manifeste resterait celui d'avant le build.
        """
        if self._manifest is not None and not force:
            return self._manifest

        res = self.invoke(["parse"], for_run=for_run)
        if res.success and res.result is not None:
            self._manifest = res.result
            self._manifest_error = None
            # Le manifeste a changé : ce que l'entrepôt gardait des colonnes
            # d'entrée ne vaut plus, un `ref()` pouvant désigner autre chose.
            self.generation += 1
        else:
            self._manifest_error = _exception_text(res)
            if self._manifest is None:
                raise RuntimeError(self._manifest_error or "dbt parse a échoué")
        return self._manifest

    @property
    def manifest_error(self) -> str | None:
        return self._manifest_error

    def manifest(self):
        return self.parse()

    # -------------------------------------------------------------- lecture

    def show(self, sql: str, limit: int = 100):
        """Exécute une requête via `dbt show --inline`. Retourne une table agate.

        C'est le seul chemin de lecture de l'atelier : Pliq n'ouvre jamais de
        connexion à l'entrepôt lui-même. Le SQL passé ici peut contenir du
        Jinja dbt — `ref()`, `source()`, macros, `var()` — c'est dbt qui le
        compile, avec le manifest du projet.
        """
        manifest = self.parse()  # pris hors du verrou : parse() le gère
        res = self.invoke(
            ["show", "--inline", sql, "--limit", str(int(limit)), "--quiet"],
            manifest=manifest,
            capture_stdout=True,
        )
        if not res.success:
            raise ShowError(_exception_text(res) or "dbt show a échoué")

        results = getattr(res.result, "results", None) or []
        if not results:
            raise ShowError("dbt show n'a rien retourné.")
        table = getattr(results[0], "agate_table", None)
        if table is None:
            raise ShowError("dbt show n'a pas retourné de table.")
        return table

    # -------------------------------------------------------- run_results

    def load_run_results(self) -> None:
        """Réhydrate l'état des nœuds depuis le dernier run sur disque."""
        f = self.settings.target_dir / "run_results.json"
        if not f.exists():
            return
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return
        # `run_results.json` est un fichier que dbt écrit, mais qu'un arrêt
        # brutal peut laisser tronqué et qu'un outil tiers peut remplacer.
        # `[]` se parse très bien et n'a pas de `.get` : l'AttributeError
        # remontait dans le `finally` du worker, avant que `running` ne
        # retombe, et l'atelier croyait ensuite à un run éternel.
        if not isinstance(data, dict):
            return
        results = data.get("results")
        if not isinstance(results, list):
            return
        for r in results:
            if not isinstance(r, dict):
                continue
            uid = r.get("unique_id")
            if not uid:
                continue
            # `dbt source freshness` écrit lui aussi dans `run_results.json`.
            # Voir `_forward` : l'état d'une source, c'est sa fraîcheur, et
            # elle a sa propre pastille.
            if str(uid).startswith("source."):
                continue
            status = (r.get("status") or "").lower()
            self.node_state[uid] = NodeState(
                status={
                    "success": "success",
                    "pass": "success",
                    "warn": "warn",
                    "error": "error",
                    "fail": "error",
                    "skipped": "skipped",
                }.get(status, "idle"),
                execution_time=r.get("execution_time"),
                message=r.get("message"),
                rows_affected=(r.get("adapter_response") or {}).get("rows_affected"),
            )

    def load_freshness(self) -> None:
        """Relit la fraîcheur des sources du dernier `dbt source freshness`.

        Une transformation qui réussit sur des données vieilles de trois
        semaines réussit quand même : le Flow doit pouvoir le dire.
        """
        f = self.settings.target_dir / "sources.json"
        if not f.exists():
            return
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        meta = data.get("metadata")
        generated = meta.get("generated_at") if isinstance(meta, dict) else None
        results = data.get("results")
        if not isinstance(results, list):
            return
        out: dict[str, dict] = {}
        for r in results:
            if not isinstance(r, dict):
                continue
            uid = r.get("unique_id")
            if not uid:
                continue
            raw_text = str(r.get("status") or "").lower()
            out[uid] = {
                "status": FRESHNESS_STATUS.get(raw_text, "idle"),
                "raw_status": raw_text,
                "max_loaded_at": r.get("max_loaded_at"),
                "snapshotted_at": r.get("snapshotted_at"),
                "age_seconds": r.get("max_loaded_at_time_ago_in_s"),
                "criteria": r.get("criteria") or {},
                "checked_at": generated,
            }
        self.freshness = out

    # --------------------------------------------------------------- runs

    def start_run(
        self,
        command: str,
        select: str = "",
        exclude: str = "",
        full_refresh: bool = False,
        on_event: Callable[[dict], None] | None = None,
        on_done: Callable[[dict], None] | None = None,
        touched: Iterable[str] = (),
    ) -> int:
        """Lance une commande dbt dans un thread. Retourne l'identifiant du run."""
        if self.run.running:
            raise RuntimeError("Une exécution est déjà en cours.")

        _selectors_only(select, "sélecteur")
        _selectors_only(exclude, "exclusion")

        self._run_counter += 1
        run_id = self._run_counter

        args = list(COMMAND_ARGS.get(command, [command]))
        if select.strip():
            args += ["--select", *select.split()]
        if exclude.strip():
            args += ["--exclude", *exclude.split()]
        if full_refresh and command in ("run", "build"):
            args.append("--full-refresh")

        self.run = RunState(
            run_id=run_id,
            command=command,
            select=select,
            running=True,
            started_at=time.time(),
        )

        # Les nœuds visés repassent en attente pour que le canevas le montre.
        for uid in touched:
            self.node_state.setdefault(uid, NodeState()).status = "queued"

        def worker():
            try:
                res = self.invoke(args, on_event=on_event, reset=True, for_run=True)
                self.run.success = bool(res.success)
                if not res.success:
                    err = _exception_text(res)
                    if err and on_event:
                        on_event(
                            {
                                "type": "log",
                                "level": "error",
                                "event": "Error",
                                "msg": err,
                                "ts": time.time(),
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                self.run.success = False
                if on_event:
                    on_event(
                        {
                            "type": "log",
                            "level": "error",
                            "event": "Error",
                            "msg": f"{type(exc).__name__}: {exc}",
                            "ts": time.time(),
                        }
                    )
            finally:
                self.run.finished_at = time.time()
                try:
                    # Le manifest peut avoir changé (nouveaux modèles, nouveau
                    # state). `for_run` : `running` ne tombe qu'après, et
                    # sans le laissez-passer ce reparse se refuserait lui-même.
                    try:
                        self.parse(force=True, for_run=True)
                    except Exception:  # noqa: BLE001
                        pass
                    # Les deux réhydratations lisent des artefacts écrits par
                    # dbt. Un `run_results.json` tronqué ou d'une forme
                    # inattendue est une lecture ratée, pas une raison de
                    # laisser l'atelier croire à un run éternel : leurs
                    # exceptions se journalisent et s'arrêtent là.
                    for load_it in (self.load_run_results, self.load_freshness):
                        try:
                            load_it()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "artefact dbt illisible (%s) : %s",
                                load_it.__name__,
                                exc,
                            )
                finally:
                    # `running` tombe en dernier, mais il tombe *toujours* :
                    # c'est lui qui retient une bascule de projet, et le
                    # laisser levé après un incident ici figeait l'atelier sur
                    # un run dont le thread était déjà mort. Le baisser avant
                    # le re-parse laisserait, à l'inverse, `release()` et le
                    # `chdir` d'une bascule arriver au milieu de celui-ci.
                    self.run.running = False
                    if on_done:
                        try:
                            on_done(self.run.summary())
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("notification de fin de run : %s", exc)

        threading.Thread(target=worker, name=f"dbt-run-{run_id}", daemon=True).start()
        return run_id

    def release(self) -> None:
        """Lâche tout ce qui tient l'entrepôt : à appeler avant de changer de projet."""
        self._manifest = None
        try:
            reset_adapters()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ compile

    def compiled_test_sql(self, node) -> str | None:
        """Le SQL d'un test, tel que dbt l'a compilé : il *sélectionne les échecs*.

        C'est la matérialisation de test qui compte les lignes à l'exécution ;
        l'artefact, lui, est la requête des lignes fautives. La rejouer montre
        donc exactement ce qui a fait échouer le test, sans avoir à activer
        `store_failures` ni à deviner quoi que ce soit.

        Deux formes de chemin : un test générique est rangé sous le YAML qui le
        déclare, un test singulier est son propre fichier.
        """
        path = getattr(node, "original_file_path", None)
        pkg = getattr(node, "package_name", None)
        name = getattr(node, "name", None)
        if not (path and pkg):
            return None
        base = self.settings.target_dir / "compiled" / pkg / path
        for f in ([base / f"{name}.sql"] if name else []) + [base]:
            if f.is_file():
                try:
                    return f.read_text()
                except OSError:
                    return None
        return None

    def compiled_sql(self, node) -> str | None:
        """SQL compilé d'un nœud, lu dans target/compiled si présent."""
        path = getattr(node, "original_file_path", None)
        pkg = getattr(node, "package_name", None)
        if not path or not pkg:
            return None
        f = self.settings.target_dir / "compiled" / pkg / path
        if f.exists():
            try:
                return f.read_text()
            except OSError:
                return None
        return None


def _exception_text(res) -> str | None:
    exc = getattr(res, "exception", None)
    if exc is None:
        return None
    return f"{type(exc).__name__}: {exc}"

"""L'atelier ouvert : le projet courant, ce qui le sert, et ce qui le protège.

`create_app` était une fermeture de 2 400 lignes autour de trois cellules —
`settings`, `svc`, `wh` — que la bascule de projet réaffectait. Rien de ce
qu'elle contenait n'était importable, donc rien n'était testable autrement
qu'à travers un client HTTP, alors qu'il y avait là de la vraie logique :
qui a le droit d'écrire ce nœud, ce nom est-il libre, ce fichier a-t-il
changé depuis qu'on l'a lu.

Les trois cellules deviennent des attributs. Un attribut se lit au moment de
l'appel, exactement comme une cellule de fermeture : la bascule vaut donc
toujours pour tout le monde, et aucune route ne change de sens. Le reste du
serveur prend l'atelier en premier argument.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import threading
from pathlib import Path

from fastapi import HTTPException

from .. import projects
from .. import recipes as rcp
from ..config import Settings, load_settings
from ..dbt_service import DbtService
from ..warehouse import Warehouse

logger = logging.getLogger("pliq")

MAX_LOG_LINES = 800

# Le projet actif est global au serveur, alors que l'atelier s'ouvre dans
# autant d'onglets qu'on veut. Sans rien de plus, un onglet resté sur le projet
# A enregistre dans le projet B dès qu'un autre onglet a basculé, et répond
# 200. Chaque requête qui écrit porte donc le projet qu'elle croit modifier,
# dans cet en-tête ; le serveur le compare au projet ouvert avant d'écrire.
PROJECT_HEADER = "x-pliq-project"

# La comparaison a lieu au fond de la route, sous le verrou de projet, et pas
# dans le middleware : entre les deux, une bascule pourrait se glisser. Une
# ContextVar porte la valeur jusque-là — anyio recopie le contexte dans le fil
# d'exécution des routes synchrones.
expected_project: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "expected_project", default=None
)

# Ces routes parlent *des* projets plutôt que dans un projet : elles ont le
# droit de porter sur autre chose que celui qui est ouvert.
PROJECT_ROUTES = "/api/projects/"


class Hub:
    """Diffusion des événements de run vers les navigateurs connectés."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, msg: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, msg)
        except RuntimeError:
            pass

    # Les événements dont la perte laisse un écran figé, par opposition aux
    # lignes de journal, dont on peut sauter quelques-unes sans conséquence.
    # Une file pleine abandonnait tous les types sans distinction : perdre un
    # `run_done` laissait un onglet croire à un build éternel, et perdre un
    # `project_changed` le laissait sur le projet précédent.
    CHECK = ("run_done", "run_start", "project_changed", "flow_changed")

    def _fanout(self, msg: dict) -> None:
        check = msg.get("type") in self.CHECK
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                if not check:
                    continue
                # On fait de la place en jetant le plus ancien, qui est une
                # ligne de journal dans l'immense majorité des cas : un
                # message de contrôle vaut mieux qu'un message de plus.
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


class Workshop:
    """Le projet ouvert, ce qui le sert, et les règles qui le gardent cohérent."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.svc = DbtService(settings)
        self.wh = Warehouse(settings, self.svc)
        self.hub = Hub()

        # `settings`, `svc` et `wh` sont réaffectés par `switch`. Une route qui
        # les lit à plusieurs moments doit voir le même projet du début à la
        # fin : sans ce verrou, ouvrir un projet pendant une sauvegarde la fait
        # atterrir dans l'autre. Réentrant parce qu'une route sérialisée
        # appelle `switch`.
        #
        # Les lectures le prennent aussi, et pas seulement les écritures. Une
        # lecture passe par `dbt show`, donc par un adaptateur, le répertoire
        # courant du process et l'état global de dbt — que la bascule ferme et
        # remplace. Le partage de cet état rend deux invocations concurrentes
        # non sûres dans un même process :
        # https://docs.getdbt.com/reference/programmatic-invocations
        #
        # Restent dehors les routes qui ne lisent qu'une cellule et n'invoquent
        # jamais dbt (`/api/project`, `/api/run`, la liste des projets) : elles
        # peuvent répondre sur l'ancien projet pendant une bascule, ce qui
        # affiche une donnée périmée l'espace d'un instant mais n'abîme rien.
        self.project_lock = threading.RLock()

        # Combien de fois le projet ouvert a changé. Un onglet qui se
        # reconnecte compare ce nombre à celui qu'il avait : s'il a bougé, il
        # a manqué au moins un `project_changed` pendant la coupure, et il
        # recharge tout plutôt que de rester sur une vue périmée.
        self.generation = 0

        # La validation des expressions suit l'entrepôt du projet, pas un
        # dialecte figé : ce qui est valide en Snowflake ne doit pas être
        # refusé ici.
        rcp.set_dialect(settings.target_config().get("type"))

    # ------------------------------------------------------------- garde-fous

    def check_project(self) -> None:
        """Refuse une écriture destinée à un autre projet que celui qui est ouvert.

        À n'appeler que sous `project_lock` : c'est ce qui rend la vérification
        et l'écriture indissociables.
        """
        expected = expected_project.get()
        if expected is None:
            return
        current_dir = str(self.settings.project_dir)
        if Path(expected).expanduser().resolve() == self.settings.project_dir.resolve():
            return
        raise HTTPException(
            409,
            f"Cet écran travaille sur « {Path(expected).name} », mais l'atelier "
            f"a ouvert « {self.settings.project_name} » ({current_dir}) — sans doute "
            f"depuis un autre onglet. Rien n'a été écrit. Rechargez la page "
            f"pour repartir du projet ouvert.",
        )

    def serialized(self, fn):
        """Interdit qu'on change de projet pendant que cette route travaille."""

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with self.project_lock:
                self.check_project()
                return fn(*args, **kwargs)

        return wrapper

    def reject_during_run(self, what: str) -> None:
        """Refuse une écriture pendant qu'un `dbt build` lit le projet.

        À n'appeler que sous `project_lock`, comme `check_project` : c'est ce
        qui rend le contrôle et l'écriture indissociables — `launch_run` part
        lui aussi sous ce verrou, donc aucun run ne peut démarrer entre les
        deux.

        Le renommage posait déjà cette règle pour lui seul, et pour la bonne
        raison : dbt lit les fichiers qu'on s'apprête à changer. Elle vaut
        pareil pour l'enregistrement d'une recipe, une suppression ou une
        fiche — réécrire un `.sql` au milieu d'un build donne un modèle
        construit à partir d'un fichier qui n'existe plus, et rien ne le dit.

        Les lectures ne passent pas par ici : celles qui ont besoin de dbt se
        heurtent à `RunInProgress`, au fond, là où elles auraient attendu ; celles
        qui n'en ont pas besoin continuent de répondre, et c'est bien tout
        l'intérêt — on doit pouvoir regarder son projet pendant qu'il se
        construit.
        """
        if self.svc.run.running:
            raise HTTPException(
                409,
                f"Une exécution dbt est en cours : elle lit les fichiers que "
                f"{what} modifie. Attendez la fin, puis réessayez.",
            )

    # -------------------------------------------------------------- démarrage

    def boot(self) -> None:
        """Le premier parse, et l'état du dernier run relu sur le disque."""
        try:
            self.svc.parse(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dbt parse a échoué : %s", exc)
        self.svc.load_run_results()
        self.svc.load_freshness()

    # ---------------------------------------------------------------- projets

    def switch(self, path: Path) -> dict:
        """Bascule l'atelier sur un autre projet dbt, sans redémarrer.

        Tout le serveur lit le projet sur cet objet : réaffecter ici suffit
        pour que toutes les routes voient le nouveau.

        À n'appeler que sous `project_lock` — les routes qui l'appellent le
        prennent par `serialized`.
        """
        if self.svc.run.running:
            raise HTTPException(
                409, "Une exécution dbt est en cours : attendez la fin."
            )
        if not projects.is_project(path):
            raise HTTPException(400, f"Aucun dbt_project.yml dans {path}.")

        # Les options du lancement sont des options de *session*, pas du projet
        # qu'on quitte : les oublier ici faisait retomber sur la cible par
        # défaut du profil, sans le dire et avec un 200. Lancé sur `prod`,
        # l'atelier annonçait `dev` après réouverture — et l'inverse, lancé sur
        # `dev` avec un profil dont le défaut est `prod`, visait la production
        # au prochain build.
        #
        # C'est `explicit_target` qu'on reconduit, et jamais `target` : le
        # second est ce que le projet *courant* a pu faire de la demande, et il
        # vaut `None` dès qu'un projet traversé ne déclarait pas cette cible.
        # Repartir de là effaçait la demande pour le reste de la session — A →
        # B → A rendait la cible par défaut de A, sans que rien ne le signale.
        try:
            fresh = load_settings(
                project_dir=str(path),
                profiles_dir=self.settings.explicit_profiles_dir,
                target=self.settings.explicit_target,
                host=self.settings.host,
                port=self.settings.port,
            )
        except SystemExit as exc:
            raise HTTPException(400, str(exc)) from None

        # Reconduire la cible ne peut pas être inconditionnel : deux projets
        # n'ont pas les mêmes sorties, et pointer `prod` sur un profil qui ne la
        # déclare pas ferait échouer chaque commande dbt. Quand le profil est
        # lisible et ne connaît pas cette cible, on retombe sur son défaut — et
        # la réponse le dit, parce qu'un changement de cible silencieux est
        # exactement ce qu'on corrige ici.
        #
        # L'abandon ne porte que sur `target`, la cible effective de ce
        # projet-ci : `explicit_target` garde la demande, et la prochaine
        # bascule vers un projet qui connaît cette cible la retrouve.
        dropped_target = None
        if fresh.target and fresh.targets() and fresh.target not in fresh.targets():
            dropped_target = fresh.target
            fresh.target = None

        # On lâche la base et l'adapter de l'ancien projet avant de changer de
        # répertoire : DuckDB n'accepte qu'un écrivain, y compris le nôtre.
        self.svc.release()
        os.chdir(fresh.project_dir)
        self.settings = fresh
        self.svc = DbtService(fresh)
        self.wh = Warehouse(fresh, self.svc)
        # Le dialecte suit le projet, comme à la création de l'atelier : sans
        # ça, la validation des formules continuait de juger le nouveau projet
        # à l'aune de l'entrepôt de l'ancien. Sous `project_lock`, qui est
        # aussi le verrou de toutes les routes qui compilent du SQL.
        rcp.set_dialect(fresh.target_config().get("type"))

        parse_error = None
        try:
            self.svc.parse(force=True)
        except Exception as exc:  # noqa: BLE001
            parse_error = str(exc)
        self.svc.load_run_results()
        # La fraîcheur se recharge avec le reste. L'oublier vidait l'écran des
        # états que `target/sources.json` portait toujours : rouvrir un projet
        # effaçait des alertes sans qu'aucune source ait bougé.
        self.svc.load_freshness()
        projects.remember(fresh.project_dir, active=True)
        self.generation += 1
        self.hub.publish(
            {
                "type": "project_changed",
                "name": fresh.project_name,
                "path": str(fresh.project_dir),
                "generation": self.generation,
            }
        )
        return {
            "opened": str(fresh.project_dir),
            "name": fresh.project_name,
            "parse_error": parse_error,
            "target": fresh.active_target(),
            # Non nul quand la cible de session n'existe pas dans ce projet :
            # l'écran doit pouvoir dire sur quoi il travaille maintenant.
            "target_dropped": dropped_target,
        }

    # ------------------------------------------------------------------- runs

    def launch_run(
        self,
        command: str,
        select: str = "",
        exclude: str = "",
        full_refresh: bool = False,
    ) -> int:
        """Lance dbt. À n'appeler que sous `project_lock` : le run qui démarre
        doit se rattacher au projet courant, et pas à celui d'une bascule qui
        se glisserait entre la lecture de `svc` et le départ du thread."""
        from .reads import node_states

        svc = self.svc  # le service du run, figé : l'attribut, lui, peut changer
        svc.run.lines = []

        def on_event(msg: dict) -> None:
            svc.run.lines.append(msg)
            if len(svc.run.lines) > MAX_LOG_LINES:
                del svc.run.lines[
                    : len(svc.run.lines) - MAX_LOG_LINES
                ]
            self.hub.publish(msg)

        def on_done(summary: dict) -> None:
            self.hub.publish(
                {
                    "type": "run_done",
                    "run": summary,
                    "states": node_states(self, svc),
                    "freshness": dict(svc.freshness),
                }
            )

        run_id = svc.start_run(
            command,
            select=select,
            exclude=exclude,
            full_refresh=full_refresh,
            on_event=on_event,
            on_done=on_done,
        )
        self.hub.publish(
            {
                "type": "run_start",
                "run_id": run_id,
                "command": command,
                "select": select,
            }
        )
        return run_id

"""L'application : on assemble, on ne code pas ici.

`create_app` était une fermeture de 2 400 lignes autour de trois cellules que
la bascule de projet réaffectait. Rien de ce qu'elle contenait n'était
importable, donc rien n'était testable autrement qu'à travers un client HTTP —
alors qu'il y avait là de la vraie logique : qui a le droit d'écrire ce nœud,
ce nom est-il libre, ce fichier a-t-il changé depuis qu'on l'a lu.

Ce qui reste ici est la composition, et rien d'autre :

    atelier         le projet ouvert, le verrou, les garde-fous, la bascule
    http            sous quel nom et depuis quelle page on accepte d'être joint
    bodies          les corps de requête
    noeuds          retrouver un nœud dbt, dire ce qu'on a le droit d'en faire
    versions        refuser d'écraser une version qu'on n'a pas lue
    lectures        ce que les routes lisent avant de répondre
    routes_*        les routes, une fabrique par domaine

`from pliq.server import create_app` s'écrit comme avant.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import recipes as rcp
from ..config import ConfigError, Settings
from ..dbt_service import RunInProgress
from . import (
    routes_data,
    routes_flow,
    routes_projects,
    routes_recipes,
    routes_runs,
)
from .workshop import PROJECT_HEADER, PROJECT_ROUTES, Workshop, expected_project
from .http import _host_allowed

STATIC = Path(__file__).parent.parent / "static"

logger = logging.getLogger("pliq")


def create_app(settings: Settings) -> FastAPI:
    a = Workshop(settings)

    @contextlib.asynccontextmanager
    async def _lifespan(_app: FastAPI):
        """Le premier parse, et rien après : l'atelier n'a rien à ranger.

        En `lifespan` plutôt qu'en `@app.on_event("startup")`, que FastAPI
        annonce retiré depuis longtemps.

        Le parse part dans un exécuteur : il prend une seconde sur un gros
        projet, et le bloquer ici retiendrait la boucle — donc le port, donc
        la page que le navigateur vient d'ouvrir.
        """
        from .. import projects

        a.hub.bind(asyncio.get_running_loop())
        await asyncio.get_running_loop().run_in_executor(None, a.boot)
        projects.remember(a.settings.project_dir, active=True)
        logger.info(
            "projet « %s » prêt — cible %s",
            a.settings.project_name,
            a.settings.active_target(),
        )
        yield

    app = FastAPI(title="Pliq", docs_url=None, redoc_url=None, lifespan=_lifespan)

    @app.middleware("http")
    async def _check_host(request, call_next):
        """Refuse de répondre sous un nom d'hôte qu'on n'attend pas.

        Même parade que sur le WebSocket, et pour la même raison : un nom que
        l'attaquant contrôle, détourné vers 127.0.0.1, ferait passer sa page
        pour la nôtre aux yeux du navigateur — et le contrôle d'origine de la
        socket tomberait avec, puisque l'origine correspondrait alors vraiment.
        """
        host = request.headers.get("host", "")
        if not _host_allowed(host, a.settings.host):
            return JSONResponse(
                {
                    "error": (
                        f"L'atelier ne répond pas sous le nom « {host} ». "
                        f"Ouvrez-le sur http://{a.settings.host}:{a.settings.port}."
                    )
                },
                status_code=421,
            )
        return await call_next(request)

    @app.middleware("http")
    async def _carry_project(request, call_next):
        """Relève le projet que la requête croit modifier, pour les écritures.

        Les lectures ne sont pas concernées : au pire elles affichent la donnée
        d'un autre projet le temps d'un rafraîchissement, ce qui n'abîme rien.
        """
        token = None
        expected = request.headers.get(PROJECT_HEADER)
        if (
            expected
            and request.method not in ("GET", "HEAD", "OPTIONS")
            and not request.url.path.startswith(PROJECT_ROUTES)
        ):
            token = expected_project.set(expected)
        try:
            return await call_next(request)
        finally:
            if token is not None:
                expected_project.reset(token)

    for module in (
        routes_flow,
        routes_recipes,
        routes_data,
        routes_projects,
        routes_runs,
    ):
        module.mount(app, a)

    # ---------------------------------------------------------------- static

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/doc")
    def doc() -> FileResponse:
        """La documentation, servie par l'atelier lui-même.

        Une adresse à elle plutôt qu'un écran de plus : `/doc?p=flow` se met
        en signet, se copie dans un ticket, et s'ouvre dans un onglet sans
        déranger le travail en cours. La page, ses feuilles et ses `.md`
        partent par le montage `/static` comme le reste — c'est la même
        promesse que les polices embarquées : rien à demander au réseau.
        """
        return FileResponse(STATIC / "doc" / "index.html")

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException) -> JSONResponse:
        # Un refus sur lequel l'écran a quelque chose à proposer se nomme :
        # `detail` est alors le corps entier, son `code` compris. Reconnaître
        # une phrase française côté navigateur serait plus fragile que ça.
        if isinstance(exc.detail, dict):
            return JSONResponse(exc.detail, status_code=exc.status_code)
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RunInProgress)
    async def run_in_progress(_request, exc: RunInProgress) -> JSONResponse:
        """Une lecture qui aurait attendu la fin d'un build se dit, elle aussi.

        Les routes qui écrivent posent la question avant de toucher au disque,
        avec `Workshop.reject_during_run`. Celles qui lisent ne peuvent pas
        la poser d'avance sans se tromper : beaucoup n'ont besoin que du
        manifeste déjà en mémoire et doivent continuer de répondre pendant un
        build. Le refus vient donc de là où il se produit vraiment —
        l'invocation dbt — et ce filet lui donne son code : 409, comme tout ce
        qui n'est pas possible *maintenant*.
        """
        return JSONResponse(
            {"error": str(exc), "code": "run_in_progress"}, status_code=409
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_body(_request, exc: RequestValidationError) -> JSONResponse:
        """Dire quel champ est refusé, et pas seulement « 422 ».

        FastAPI range ses erreurs de validation dans `detail`, alors que tout
        le reste de l'API — et le client, qui ne lit que `body.error` —
        emploie `error`. Un corps mal formé s'affichait donc « 422
        Unprocessable Entity », l'explication jetée : selon l'endroit exact de
        la malformation, l'utilisateur recevait une phrase française
        (validation de l'atelier, 400) ou un code nu (Pydantic, 422).
        """
        sentences = []
        for err in exc.errors():
            path = ".".join(
                str(p) for p in err.get("loc", ()) if p not in ("body", "query")
            )
            msg = err.get("msg", "valeur invalide")
            sentences.append(f"« {path} » : {msg}" if path else msg)
        return JSONResponse(
            {"error": " ; ".join(sentences) or "Corps de requête invalide."},
            status_code=422,
        )

    @app.exception_handler(ConfigError)
    async def config_error(_request, exc: ConfigError) -> JSONResponse:
        """Un fichier du projet illisible est un refus, pas une panne.

        `dbt_project.yml` ou `profiles.yml` qui n'est pas une table, chemin
        sorti de l'arbre, YAML cassé : la seule chose à faire est d'aller
        réparer ce fichier-là, et il faut donc le nommer. Couvre aussi
        `files.FileError`, qui en dérive.
        """
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(rcp.RecipeError)
    async def recipe_error(_request, exc: rcp.RecipeError) -> JSONResponse:
        """Le refus d'une recipe est une erreur de requête, pas une panne.

        La plupart des routes attrapent déjà la leur pour l'assortir d'un
        message de contexte. Ce filet vaut pour ce qui remonte d'ailleurs — le
        stockage qui refuse un chemin sorti du projet, par exemple — et évite
        qu'un refus délibéré ne se présente comme un 500.
        """
        return JSONResponse({"error": str(exc)}, status_code=400)

    return app

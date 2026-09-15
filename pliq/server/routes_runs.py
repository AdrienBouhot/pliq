"""Lancer dbt, suivre son journal."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from ..dbt_service import SelectionError
from .workshop import Workshop
from .bodies import RunBody
from .http import _host_allowed, _same_origin
from .reads import node_states


def mount(app: FastAPI, a: Workshop) -> None:
    @app.post("/api/run")
    @a.serialized
    def start_run(body: RunBody) -> dict:
        allowed = {
            "build",
            "run",
            "test",
            "seed",
            "compile",
            "parse",
            "snapshot",
            "deps",
            "freshness",
        }
        if body.command not in allowed:
            raise HTTPException(400, f"Commande non autorisée : {body.command}")
        if a.svc.run.running:
            raise HTTPException(409, "Une exécution est déjà en cours.")
        # `dbt deps` installe des paquets : il ne sélectionne pas des nœuds, et
        # un `--select` traînant le ferait échouer sur un argument inconnu.
        select = "" if body.command == "deps" else body.select
        exclude = "" if body.command == "deps" else body.exclude
        try:
            run_id = a.launch_run(body.command, select, exclude, body.full_refresh)
        except SelectionError as exc:
            # Avant `RuntimeError` : c'est une `ValueError`, et la demande est
            # malformée, pas en conflit avec un run en cours.
            raise HTTPException(400, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"run_id": run_id, "command": body.command, "select": select}

    @app.get("/api/run")
    def run_status() -> dict:
        return {
            "run": a.svc.run.summary(),
            # `list()` et `dict()` pour la même raison que `node_states` :
            # le worker ajoute des lignes et en coupe la tête pendant qu'on
            # sérialise, et une liste qui rétrécit sous le sérialiseur est un
            # 500 au milieu d'un build.
            "lines": list(a.svc.run.lines),
            "states": node_states(a),
            "freshness": dict(a.svc.freshness),
        }

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        """Suivi en direct : on pousse les événements de run vers le navigateur.

        On écoute la socket *en parallèle* de la file d'événements, alors que
        le navigateur n'envoie jamais rien. C'est que la déconnexion arrive par
        là — celle du navigateur, mais aussi celle qu'uvicorn adresse à chaque
        WebSocket quand l'atelier s'arrête. À n'attendre que la file, on attend
        un message qui ne viendra jamais : uvicorn ferme le port, puis attend
        la fin de cette connexion. L'atelier reste alors en vie sans répondre à
        personne, et l'onglet ouvert ne récolte plus que des erreurs réseau.

        Avant tout cela, l'origine : la poignée de main est le seul moment où
        l'on peut encore refuser. Une fois `accept()` passé, le message `hello`
        est déjà parti avec l'état du run et des nœuds.
        """
        headers = socket.headers
        host = headers.get("host", "")
        if not _host_allowed(host, a.settings.host) or not _same_origin(
            headers.get("origin", ""), host
        ):
            # 1008 « policy violation ». Fermer sans accepter : le navigateur
            # de la page fautive voit un échec de connexion, et rien d'autre.
            await socket.close(code=1008)
            return

        await socket.accept()
        q = a.hub.subscribe()
        listening = asyncio.ensure_future(socket.receive())
        try:
            await socket.send_json(
                {
                    "type": "hello",
                    "run": a.svc.run.summary(),
                    "states": node_states(a),
                    # De quoi savoir, *à la reconnexion*, si l'écran regarde
                    # encore le bon projet et le bon run. Un onglet qui a
                    # manqué un `project_changed` ou un `run_done` pendant une
                    # coupure restait sinon sur une vue périmée sans que rien
                    # ne le lui dise : il compare maintenant ces trois valeurs
                    # à ce qu'il affiche, et recharge si elles ont bougé.
                    "project": a.settings.project_name,
                    "project_dir": str(a.settings.project_dir),
                    "freshness": dict(a.svc.freshness),
                    "generation": a.generation,
                }
            )
            while True:
                pending = asyncio.ensure_future(q.get())
                done, _ = await asyncio.wait(
                    {listening, pending}, return_when=asyncio.FIRST_COMPLETED
                )
                if (
                    listening in done
                    and listening.result()["type"] == "websocket.disconnect"
                ):
                    pending.cancel()
                    break
                if pending in done:
                    await socket.send_json(pending.result())
                else:
                    pending.cancel()
                if listening in done:  # le navigateur a parlé : on se remet à l'écoute
                    listening = asyncio.ensure_future(socket.receive())
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            listening.cancel()
            a.hub.unsubscribe(q)

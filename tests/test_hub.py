"""Le Hub : diffusion des événements de run, et ce qu'il refuse de perdre."""

from __future__ import annotations

import asyncio

from pliq.server.workshop import Hub


def test_un_run_start_survit_a_une_file_pleine():
    """Une file d'abonné saturée jette une ligne de journal, jamais le run_start.

    Verrouille la correspondance entre les chaînes de `CHECK` et les types
    réellement publiés : `run_start` doit être préservé comme `run_done` et
    `project_changed`, pas traité comme une ligne de journal jetable.
    """

    async def scenario() -> None:
        hub = Hub()
        q: asyncio.Queue = asyncio.Queue(maxsize=3)
        hub._subs.add(q)
        for i in range(3):
            q.put_nowait({"type": "log", "line": i})

        hub._fanout({"type": "run_start"})

        types = [q.get_nowait()["type"] for _ in range(q.qsize())]
        assert "run_start" in types

    asyncio.run(scenario())


def test_une_ligne_de_journal_est_abandonnee_sur_file_pleine():
    """Le pendant : un simple log est bien jeté quand la file est pleine."""

    async def scenario() -> None:
        hub = Hub()
        q: asyncio.Queue = asyncio.Queue(maxsize=3)
        hub._subs.add(q)
        for i in range(3):
            q.put_nowait({"type": "log", "line": i})

        hub._fanout({"type": "log", "line": 99})

        lines = [q.get_nowait()["line"] for _ in range(q.qsize())]
        assert 99 not in lines

    asyncio.run(scenario())

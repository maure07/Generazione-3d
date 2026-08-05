"""WebSocket per l'avanzamento in tempo reale.

Il frontend apre una connessione su ``/ws/jobs`` (opzionalmente filtrata per
job o progetto) e riceve gli eventi della pipeline man mano che accadono:
inizio passo, avanzamento, completamento, fine lavoro.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ...domain.events import bus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tempo reale"])

#: Intervallo di ping per tenere viva la connessione.
PING_INTERVAL_S = 25.0


@router.websocket("/ws/jobs")
async def jobs_socket(
    websocket: WebSocket,
    job_id: str | None = Query(None, description="Filtra su un singolo job"),
    project_id: str | None = Query(None, description="Filtra su un singolo progetto"),
) -> None:
    """Trasmette gli eventi della pipeline al client."""
    await websocket.accept()
    logger.debug("WebSocket connessa (job=%s progetto=%s)", job_id, project_id)

    # Invia subito gli ultimi eventi: la UI si riaggancia dopo un reload
    # senza dover attendere il prossimo passo della pipeline.
    for event in bus.recent(limit=20):
        if job_id and event.job_id != job_id:
            continue
        if project_id and event.project_id != project_id:
            continue
        await websocket.send_json(event.to_dict())

    async def pump() -> None:
        async for event in bus.subscribe(job_id=job_id, project_id=project_id):
            await websocket.send_json(event.to_dict())

    async def keepalive() -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_S)
            await websocket.send_json({"type": "ping"})

    pump_task = asyncio.create_task(pump())
    ping_task = asyncio.create_task(keepalive())

    try:
        # Resta in ascolto anche del client: la chiusura arriva da qui.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug("WebSocket chiusa dal client")
    except Exception as exc:  # pragma: no cover
        logger.debug("WebSocket interrotta: %s", exc)
    finally:
        pump_task.cancel()
        ping_task.cancel()
        for task in (pump_task, ping_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

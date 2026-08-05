"""Bus eventi asincrono usato per lo streaming del progresso verso l'interfaccia.

Il bus è volutamente minimale (nessuna dipendenza esterna): la pipeline pubblica
eventi, le connessioni WebSocket vi si iscrivono. È thread-safe rispetto al
loop asyncio perché ogni sottoscrittore possiede la propria `asyncio.Queue`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 512


@dataclass(slots=True)
class Event:
    """Evento generico pubblicato dalla pipeline."""

    type: str
    job_id: str | None = None
    project_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


class EventBus:
    """Publish/subscribe in-process con code per sottoscrittore.

    Esempio::

        bus = EventBus()
        async for event in bus.subscribe(job_id="abc"):
            print(event.type)
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._lock = asyncio.Lock()
        self._history: list[Event] = []
        self._history_limit = 1000

    async def publish(self, event: Event) -> None:
        """Invia un evento a tutti i sottoscrittori (non blocca mai)."""
        self._history.append(event)
        if len(self._history) > self._history_limit:
            del self._history[: len(self._history) - self._history_limit]

        async with self._lock:
            dead: list[asyncio.Queue[Event]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Un consumatore lento non deve rallentare la pipeline.
                    logger.warning("Coda eventi piena: sottoscrittore rimosso")
                    dead.append(queue)
            for queue in dead:
                self._subscribers.discard(queue)

    def publish_nowait(self, event: Event) -> None:
        """Variante sincrona: utile dai worker in thread pool."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Nessun loop attivo (es. esecuzione da CLI o test sincroni).
            self._history.append(event)
            return
        loop.create_task(self.publish(event))

    @contextlib.asynccontextmanager
    async def _queue(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def subscribe(
        self, job_id: str | None = None, project_id: str | None = None
    ) -> AsyncIterator[Event]:
        """Iteratore asincrono sugli eventi, opzionalmente filtrati."""
        async with self._queue() as queue:
            while True:
                event = await queue.get()
                if job_id is not None and event.job_id != job_id:
                    continue
                if project_id is not None and event.project_id != project_id:
                    continue
                yield event

    def recent(self, limit: int = 50) -> list[Event]:
        """Ultimi eventi pubblicati (utile per riagganciare la UI dopo un reload)."""
        return self._history[-limit:]


#: Bus condiviso a livello di applicazione.
bus = EventBus()

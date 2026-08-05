"""Salvataggio automatico periodico dei progetti modificati.

L'utente non deve mai perdere lavoro per una chiusura imprevista. Il servizio
tiene traccia dei progetti "sporchi" e li scrive su disco a intervalli
regolari, evitando di riscrivere quelli non modificati.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import get_settings
from ..domain.models import Project
from .store import ProjectStore, store as default_store

logger = logging.getLogger(__name__)


class AutosaveService:
    """Servizio di salvataggio automatico in background."""

    def __init__(self, store: ProjectStore | None = None, interval_s: float | None = None) -> None:
        self.store = store or default_store
        self.interval_s = interval_s or get_settings().autosave_interval_s
        self._dirty: dict[str, Project] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def mark_dirty(self, project: Project) -> None:
        """Segnala che un progetto va salvato al prossimo giro."""
        self._dirty[project.id] = project

    async def start(self) -> None:
        """Avvia il ciclo di salvataggio."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="autosave")
        logger.info("Salvataggio automatico attivo ogni %.0f s", self.interval_s)

    async def stop(self) -> None:
        """Ferma il ciclo salvando un'ultima volta."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()

    async def flush(self) -> int:
        """Salva subito tutti i progetti in attesa. Ritorna quanti ne ha scritti."""
        async with self._lock:
            pending = list(self._dirty.values())
            self._dirty.clear()

        saved = 0
        for project in pending:
            try:
                # Nessuno snapshot: l'autosave non deve inquinare l'undo/redo.
                self.store.save(project)
                saved += 1
            except Exception:  # pragma: no cover
                logger.exception("Salvataggio automatico di %s fallito", project.id[:8])
        if saved:
            logger.debug("Salvataggio automatico: %d progetti scritti", saved)
        return saved

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.interval_s)
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                logger.exception("Ciclo di salvataggio automatico interrotto, riprendo")


#: Servizio condiviso a livello di applicazione.
autosave = AutosaveService()

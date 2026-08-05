"""API pubblica per i plugin di PrintReady AI.

Un plugin è un modulo Python che espone una funzione ``register(api)``. Riceve
un oggetto ``PluginAPI`` con tutti i punti di estensione dell'applicazione e
può aggiungere provider AI, regole di stampabilità, esportatori, passi della
pipeline e termini al lessico semantico.

Esempio minimo (``~/PrintReadyAI/plugins/mio_plugin.py``)::

    from printready.domain.enums import PartType

    PLUGIN_NAME = "Termini fantasy"
    PLUGIN_VERSION = "1.0"

    def register(api):
        api.add_semantic_terms(PartType.WEAPON, {"bastone runico", "grimorio"})
        api.log("Lessico fantasy caricato")

I plugin sono codice Python eseguito con i privilegi dell'applicazione:
installare solo plugin di cui ci si fida.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..domain.enums import ExportFormat, IssueCode, PartType, StepId

logger = logging.getLogger(__name__)


@dataclass
class PluginInfo:
    """Metadati di un plugin caricato."""

    name: str
    version: str = "1.0"
    author: str = ""
    description_it: str = ""
    module: str = ""
    enabled: bool = True
    error_it: str | None = None
    contributions: list[str] = field(default_factory=list)


class PluginAPI:
    """Punti di estensione offerti ai plugin."""

    def __init__(self, info: PluginInfo) -> None:
        self._info = info

    # -- registrazioni -----------------------------------------------------

    def add_ai_provider(self, provider) -> None:
        """Registra un generatore immagine → 3D personalizzato."""
        from ..ai.registry import registry

        registry.register(provider)
        self._record(f"provider AI «{provider.name}»")

    def add_printability_rule(self, code: IssueCode, rule: Callable) -> None:
        """Registra una regola di controllo della stampabilità."""
        from ..printability.rules import register_rule

        register_rule(code, rule)
        self._record(f"regola di stampabilità «{code.value}»")

    def add_exporter(self, fmt: ExportFormat, exporter_class: type) -> None:
        """Registra un esportatore per un formato."""
        from ..exporters import register_exporter

        register_exporter(fmt, exporter_class)
        self._record(f"esportatore «{fmt.value}»")

    def add_pipeline_step(self, after: StepId, step_class: type) -> None:
        """Inserisce un passo personalizzato nella pipeline."""
        from ..pipeline.jobs import job_manager

        job_manager.orchestrator.insert_step_after(after, step_class)
        self._record(f"passo di pipeline dopo «{after.value}»")

    def add_semantic_terms(self, part: PartType, terms: set[str]) -> None:
        """Estende il lessico di riconoscimento delle parti."""
        from ..ai.semantic import register_terms

        register_terms(part, terms)
        self._record(f"{len(terms)} termini per «{part.value}»")

    def on_event(self, event_type: str, handler: Callable[[Any], None]) -> None:
        """Registra un ascoltatore di eventi della pipeline."""
        import asyncio

        from ..domain.events import bus

        async def listen() -> None:
            async for event in bus.subscribe():
                if event.type == event_type:
                    try:
                        handler(event)
                    except Exception:  # pragma: no cover
                        logger.exception("Handler del plugin %s fallito", self._info.name)

        try:
            asyncio.get_running_loop().create_task(listen())
            self._record(f"ascoltatore per «{event_type}»")
        except RuntimeError:
            logger.warning(
                "Plugin %s: nessun loop attivo, ascoltatore «%s» non registrato",
                self._info.name,
                event_type,
            )

    # -- utilità -----------------------------------------------------------

    def log(self, message: str) -> None:
        """Scrive un messaggio nel log applicativo, prefissato dal plugin."""
        logger.info("[plugin %s] %s", self._info.name, message)

    def data_dir(self):
        """Cartella dove il plugin può salvare i propri dati."""
        from ..config import get_settings

        path = get_settings().plugins_dir / "dati" / self._info.name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def settings(self):
        """Impostazioni globali dell'applicazione (sola lettura)."""
        from ..config import get_settings

        return get_settings()

    def _record(self, contribution: str) -> None:
        self._info.contributions.append(contribution)
        logger.debug("Plugin %s ha aggiunto: %s", self._info.name, contribution)

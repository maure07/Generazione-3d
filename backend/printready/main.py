"""Applicazione FastAPI di PrintReady AI.

Il backend gira in locale (``127.0.0.1``) ed è consumato dall'interfaccia
desktop Electron. Espone l'API REST, il WebSocket di avanzamento e la
documentazione interattiva su ``/docs``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import ALL_ROUTERS
from .config import get_settings
from .logging_setup import setup_logging
from .plugins import plugin_manager
from .projects import ProjectNotFound, autosave

logger = logging.getLogger(__name__)

DESCRIPTION = """
API di **PrintReady AI**: da una immagine e una descrizione a un modello 3D
diviso in pezzi, con incastri, colori AMS e file pronti per lo slicer.

Flusso tipico:

1. `POST /api/projects` — crea il progetto
2. `POST /api/projects/{id}/images` — carica una o più immagini
3. `PATCH /api/projects/{id}` — imposta la descrizione e le preferenze
4. `POST /api/generate` — avvia la pipeline
5. `ws://…/ws/jobs?job_id=…` — segue l'avanzamento in tempo reale
6. `GET /api/mesh/{job_id}/files` — scarica i file prodotti
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Avvio e arresto ordinati dei servizi di supporto."""
    settings = get_settings()
    setup_logging()
    logger.info("Avvio di %s %s", settings.app_name, settings.version)
    logger.info("Cartella dati: %s", settings.data_dir)

    plugin_manager.load_all()
    await autosave.start()

    yield

    logger.info("Arresto in corso: salvataggio dei progetti in sospeso")
    await autosave.stop()
    logger.info("Arresto completato")


def create_app() -> FastAPI:
    """Costruisce l'applicazione FastAPI."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in ALL_ROUTERS:
        app.include_router(router)

    _register_error_handlers(app)

    @app.get("/api/health", tags=["stato"], summary="Stato del servizio")
    def health() -> dict:
        """Verifica rapida che il backend risponda."""
        from .mesh.booleans import engine_name

        return {
            "stato": "attivo",
            "applicazione": settings.app_name,
            "versione": settings.version,
            "motore_booleano": engine_name(),
            "provider_configurati": settings.configured_providers(),
        }

    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Traduce le eccezioni di dominio in risposte HTTP comprensibili."""
    from .ai.base import ProviderError
    from .exporters.base import ExportError
    from .mesh.io import MeshLoadError

    @app.exception_handler(ProjectNotFound)
    async def project_not_found(request: Request, exc: ProjectNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError) -> JSONResponse:
        # 502: il problema è a monte, in un servizio esterno.
        status = 400 if not exc.recoverable else 502
        return JSONResponse(status_code=status, content={"detail": exc.message_it})

    @app.exception_handler(MeshLoadError)
    async def mesh_load_error(request: Request, exc: MeshLoadError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ExportError)
    async def export_error(request: Request, exc: ExportError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Errore non gestito su %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "Errore interno del server. I dettagli sono nel file di log "
                    "dell'applicazione."
                )
            },
        )


#: Istanza usata da uvicorn (``uvicorn printready.main:app``).
app = create_app()

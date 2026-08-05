"""Router dell'API REST e WebSocket."""

from . import generation, mesh, projects, settings, ws  # noqa: F401

#: Router da includere nell'applicazione, nell'ordine di registrazione.
ALL_ROUTERS = [
    projects.router,
    generation.router,
    mesh.router,
    settings.router,
    ws.router,
]

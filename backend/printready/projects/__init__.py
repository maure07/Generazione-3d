"""Gestione dei progetti: persistenza, cronologia, undo/redo, autosave."""

from .autosave import AutosaveService, autosave  # noqa: F401
from .store import ProjectNotFound, ProjectStore, store  # noqa: F401

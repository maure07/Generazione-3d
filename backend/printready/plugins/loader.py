"""Caricamento dei plugin dalla cartella dati.

I plugin vengono cercati in ``<dati>/plugins`` come singoli file ``.py`` o come
pacchetti (cartelle con ``__init__.py``). Ogni plugin viene caricato in
isolamento: un errore in uno non impedisce il caricamento degli altri.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from ..config import get_settings
from .api import PluginAPI, PluginInfo

logger = logging.getLogger(__name__)


class PluginManager:
    """Individua, carica e descrive i plugin installati."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or get_settings().plugins_dir
        self.directory.mkdir(parents=True, exist_ok=True)
        self.plugins: list[PluginInfo] = []

    def discover(self) -> list[Path]:
        """Elenca i file e i pacchetti candidati a essere plugin."""
        candidates: list[Path] = []
        for entry in sorted(self.directory.iterdir()):
            if entry.name.startswith(("_", ".")) or entry.name == "dati":
                continue
            if entry.is_file() and entry.suffix == ".py":
                candidates.append(entry)
            elif entry.is_dir() and (entry / "__init__.py").exists():
                candidates.append(entry / "__init__.py")
        return candidates

    def load_all(self) -> list[PluginInfo]:
        """Carica tutti i plugin trovati e restituisce l'esito di ognuno."""
        if not get_settings().enable_plugins:
            logger.info("Sistema dei plugin disattivato dalle impostazioni")
            return []

        self.plugins = []
        for path in self.discover():
            self.plugins.append(self._load_one(path))

        caricati = sum(1 for p in self.plugins if p.enabled and p.error_it is None)
        if self.plugins:
            logger.info("Plugin caricati: %d su %d", caricati, len(self.plugins))
        return self.plugins

    def _load_one(self, path: Path) -> PluginInfo:
        """Carica un singolo plugin, catturandone gli errori."""
        module_name = f"printready_plugin_{path.parent.name if path.name == '__init__.py' else path.stem}"
        info = PluginInfo(name=module_name, module=str(path))

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                info.error_it = "Modulo non caricabile"
                info.enabled = False
                return info

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            info.error_it = f"Errore durante l'importazione: {exc}"
            info.enabled = False
            logger.warning("Plugin %s non caricato: %s", path.name, exc)
            return info

        info.name = getattr(module, "PLUGIN_NAME", module_name)
        info.version = str(getattr(module, "PLUGIN_VERSION", "1.0"))
        info.author = str(getattr(module, "PLUGIN_AUTHOR", ""))
        info.description_it = str(getattr(module, "PLUGIN_DESCRIPTION_IT", ""))

        register = getattr(module, "register", None)
        if not callable(register):
            info.error_it = "Il plugin non espone la funzione register(api)"
            info.enabled = False
            return info

        try:
            register(PluginAPI(info))
        except Exception as exc:
            info.error_it = f"Errore in register(): {exc}"
            info.enabled = False
            logger.warning("register() del plugin %s fallita: %s", info.name, exc)
            return info

        logger.info("Plugin caricato: %s v%s", info.name, info.version)
        return info

    def describe(self) -> list[dict]:
        """Descrizione dei plugin per l'interfaccia."""
        return [
            {
                "name": p.name,
                "version": p.version,
                "author": p.author,
                "description_it": p.description_it,
                "enabled": p.enabled,
                "error_it": p.error_it,
                "contributions": p.contributions,
            }
            for p in self.plugins
        ]


#: Gestore condiviso a livello di applicazione.
plugin_manager = PluginManager()

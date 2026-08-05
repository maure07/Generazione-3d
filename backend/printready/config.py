"""Configurazione applicativa centralizzata.

I valori possono essere sovrascritti da variabili d'ambiente con prefisso
``PRINTREADY_`` oppure da un file ``.env`` nella cartella di lavoro.
Le chiavi API non vengono mai serializzate verso il frontend: l'endpoint
``/api/settings`` restituisce solo il flag "configurata".
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    """Cartella dati per piattaforma (su Windows: ``%LOCALAPPDATA%``)."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "PrintReadyAI"


class Settings(BaseSettings):
    """Impostazioni globali del backend."""

    model_config = SettingsConfigDict(
        env_prefix="PRINTREADY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server -----------------------------------------------------------
    app_name: str = "PrintReady AI"
    version: str = "1.0.0"
    host: str = "127.0.0.1"
    port: int = 8765
    reload: bool = False
    log_level: str = "INFO"
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "app://.",
        ]
    )

    # --- Percorsi ---------------------------------------------------------
    data_dir: Path = Field(default_factory=default_data_dir)

    # --- Provider AI ------------------------------------------------------
    tripo_api_key: str = ""
    tripo_base_url: str = "https://api.tripo3d.ai/v2/openapi"
    meshy_api_key: str = ""
    meshy_base_url: str = "https://api.meshy.ai/openapi"
    hunyuan_endpoint: str = ""
    hunyuan_api_key: str = ""
    ai_timeout_s: float = 900.0
    ai_poll_interval_s: float = 3.0

    # --- Calcolo ----------------------------------------------------------
    use_gpu: bool = True
    cuda_device: int = 0
    max_workers: int = 4
    max_parallel_jobs: int = 2

    # --- Funzionalità -----------------------------------------------------
    autosave_interval_s: float = 20.0
    history_limit: int = 0  # 0 = illimitato
    enable_plugins: bool = True
    enable_ota: bool = True
    ota_manifest_url: str = ""
    telemetry: bool = False

    # --- Percorsi derivati ------------------------------------------------
    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def plugins_dir(self) -> Path:
        return self.data_dir / "plugins"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        """Crea l'albero delle cartelle dati se mancante."""
        for path in (
            self.data_dir,
            self.projects_dir,
            self.cache_dir,
            self.exports_dir,
            self.uploads_dir,
            self.plugins_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def configured_providers(self) -> dict[str, bool]:
        """Mappa provider -> credenziali presenti (senza esporre le chiavi)."""
        return {
            "tripo": bool(self.tripo_api_key),
            "meshy": bool(self.meshy_api_key),
            "hunyuan3d": bool(self.hunyuan_endpoint),
            "local": True,  # il generatore locale è sempre disponibile
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Istanza singleton delle impostazioni."""
    settings = Settings()
    settings.ensure_dirs()
    return settings

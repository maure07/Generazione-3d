"""Aggiornamenti OTA (over-the-air).

Il servizio interroga un manifesto JSON remoto, confronta la versione con
quella installata e — se richiesto — scarica il pacchetto verificandone
l'integrità con SHA-256.

**L'installazione non è automatica.** L'applicazione scarica e verifica, poi
avvisa l'utente: sostituire i binari di un'applicazione desktop mentre è in
esecuzione è un'operazione che deve restare una scelta esplicita.

Formato del manifesto atteso::

    {
      "version": "1.1.0",
      "release_date": "2026-09-01",
      "notes_it": "Nuovo segmentatore per figure animali",
      "mandatory": false,
      "packages": {
        "win32": {
          "url": "https://esempio/PrintReadyAI-1.1.0-win64.exe",
          "sha256": "…",
          "size_bytes": 148000000
        }
      }
    }
"""

from __future__ import annotations

import hashlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UpdateInfo:
    """Esito del controllo aggiornamenti."""

    available: bool = False
    current_version: str = ""
    latest_version: str = ""
    release_date: str = ""
    notes_it: str = ""
    mandatory: bool = False
    download_url: str = ""
    sha256: str = ""
    size_bytes: int = 0
    error_it: str | None = None

    def message_it(self) -> str:
        if self.error_it:
            return f"Controllo aggiornamenti non riuscito: {self.error_it}"
        if not self.available:
            return f"PrintReady AI {self.current_version} è aggiornato"
        prefisso = "Aggiornamento obbligatorio" if self.mandatory else "Aggiornamento disponibile"
        return f"{prefisso}: versione {self.latest_version} ({self.release_date})"


def parse_version(value: str) -> tuple[int, ...]:
    """Converte ``1.2.3`` in ``(1, 2, 3)`` per il confronto ordinato."""
    parts: list[int] = []
    for chunk in value.strip().lstrip("vV").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    """``True`` se ``candidate`` è una versione successiva a ``current``."""
    return parse_version(candidate) > parse_version(current)


def platform_key() -> str:
    """Chiave della piattaforma corrente nel manifesto."""
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


class UpdateService:
    """Controllo e scaricamento degli aggiornamenti."""

    def __init__(self, manifest_url: str | None = None) -> None:
        settings = get_settings()
        self.manifest_url = manifest_url or settings.ota_manifest_url
        self.current_version = settings.version
        self.enabled = settings.enable_ota

    async def check(self) -> UpdateInfo:
        """Interroga il manifesto remoto e confronta le versioni."""
        info = UpdateInfo(current_version=self.current_version)

        if not self.enabled:
            info.error_it = "Aggiornamenti automatici disattivati nelle impostazioni"
            return info
        if not self.manifest_url:
            info.error_it = "Nessun indirizzo di aggiornamento configurato"
            return info

        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(self.manifest_url)
                response.raise_for_status()
                manifest = response.json()
        except httpx.HTTPError as exc:
            info.error_it = f"server non raggiungibile ({exc.__class__.__name__})"
            return info
        except ValueError:
            info.error_it = "il manifesto ricevuto non è un JSON valido"
            return info

        info.latest_version = str(manifest.get("version", ""))
        info.release_date = str(manifest.get("release_date", ""))
        info.notes_it = str(manifest.get("notes_it") or manifest.get("notes", ""))
        info.mandatory = bool(manifest.get("mandatory", False))

        if not info.latest_version:
            info.error_it = "il manifesto non indica alcuna versione"
            return info

        info.available = is_newer(info.latest_version, self.current_version)
        if not info.available:
            return info

        package = (manifest.get("packages") or {}).get(platform_key())
        if not package:
            info.available = False
            info.error_it = f"nessun pacchetto disponibile per {platform_key()}"
            return info

        info.download_url = str(package.get("url", ""))
        info.sha256 = str(package.get("sha256", ""))
        info.size_bytes = int(package.get("size_bytes", 0) or 0)

        logger.info("Aggiornamento trovato: %s", info.message_it())
        return info

    async def download(self, info: UpdateInfo, progress=None) -> Path:
        """Scarica il pacchetto e ne verifica l'impronta SHA-256.

        Args:
            info: esito del controllo, con URL e impronta attesa.
            progress: callback ``(frazione, messaggio)`` opzionale.

        Returns:
            Percorso del file scaricato.

        Raises:
            RuntimeError: se il download fallisce o l'impronta non corrisponde.
        """
        if not info.download_url:
            raise RuntimeError("Nessun pacchetto da scaricare")

        destination = get_settings().cache_dir / "aggiornamenti"
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / Path(info.download_url).name

        digest = hashlib.sha256()
        downloaded = 0

        try:
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
                async with client.stream("GET", info.download_url) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", info.size_bytes) or 0)
                    with target.open("wb") as handle:
                        async for chunk in response.aiter_bytes(chunk_size=1 << 18):
                            handle.write(chunk)
                            digest.update(chunk)
                            downloaded += len(chunk)
                            if progress and total:
                                progress(
                                    downloaded / total,
                                    f"Scaricamento aggiornamento: "
                                    f"{downloaded / 1e6:.0f}/{total / 1e6:.0f} MB",
                                )
        except httpx.HTTPError as exc:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"Scaricamento dell'aggiornamento fallito: {exc}") from exc

        if info.sha256:
            actual = digest.hexdigest()
            if actual.lower() != info.sha256.lower():
                target.unlink(missing_ok=True)
                raise RuntimeError(
                    "Il file scaricato non supera la verifica di integrità: "
                    "l'aggiornamento è stato scartato"
                )
            logger.info("Impronta SHA-256 dell'aggiornamento verificata")
        else:
            logger.warning(
                "Il manifesto non indica l'impronta SHA-256: impossibile verificare il pacchetto"
            )

        return target


#: Servizio condiviso a livello di applicazione.
update_service = UpdateService()

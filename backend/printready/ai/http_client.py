"""Utilità HTTP condivise dai provider cloud.

Centralizza timeout, ritentativi con backoff esponenziale e scaricamento dei
file risultato, così ogni provider si concentra solo sul proprio protocollo.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from .base import ProviderError

logger = logging.getLogger(__name__)

#: Codici HTTP per cui vale la pena ritentare.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    max_retries: int = 4,
    **kwargs: Any,
) -> dict[str, Any]:
    """Esegue una richiesta HTTP ritentando gli errori transitori.

    Args:
        client: client httpx già configurato con header di autenticazione.
        method: verbo HTTP.
        url: URL assoluto o relativo alla ``base_url`` del client.
        provider: nome del provider, usato nei messaggi d'errore.
        max_retries: numero massimo di tentativi.

    Raises:
        ProviderError: quando tutti i tentativi falliscono.
    """
    delay = 1.5
    last_error = ""

    for attempt in range(1, max_retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            last_error = "timeout di rete"
        except httpx.HTTPError as exc:
            last_error = f"errore di rete ({exc.__class__.__name__})"
        else:
            if response.status_code < 400:
                if not response.content:
                    return {}
                try:
                    return response.json()
                except ValueError:
                    return {"raw": response.text}

            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code in (401, 403):
                raise ProviderError(
                    f"Credenziali {provider} non valide o scadute",
                    provider=provider,
                    recoverable=False,
                )
            if response.status_code not in RETRYABLE_STATUS:
                raise ProviderError(
                    f"Il servizio {provider} ha rifiutato la richiesta ({last_error})",
                    provider=provider,
                    recoverable=False,
                )

        if attempt < max_retries:
            logger.warning(
                "Tentativo %d/%d verso %s fallito (%s): riprovo fra %.1fs",
                attempt,
                max_retries,
                provider,
                last_error,
                delay,
            )
            await asyncio.sleep(delay)
            delay *= 2

    raise ProviderError(
        f"Impossibile contattare {provider} dopo {max_retries} tentativi ({last_error})",
        provider=provider,
    )


async def download_file(
    url: str, destination: Path, *, provider: str, timeout: float = 300.0, headers: dict[str, str] | None = None
) -> Path:
    """Scarica un file in streaming, creando le cartelle necessarie."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers or {}) as response:
                if response.status_code >= 400:
                    raise ProviderError(
                        f"Download del modello da {provider} fallito (HTTP {response.status_code})",
                        provider=provider,
                    )
                with destination.open("wb") as handle:
                    async for chunk in response.aiter_bytes(chunk_size=1 << 16):
                        handle.write(chunk)
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"Download del modello da {provider} interrotto: {exc}", provider=provider
        ) from exc

    if destination.stat().st_size == 0:
        raise ProviderError(f"Il file scaricato da {provider} è vuoto", provider=provider)
    return destination


def guess_extension(url: str, default: str = ".glb") -> str:
    """Deduce l'estensione del modello dall'URL di download."""
    lowered = url.split("?")[0].lower()
    for extension in (".glb", ".gltf", ".obj", ".stl", ".ply", ".fbx", ".usdz", ".3mf"):
        if lowered.endswith(extension):
            return extension
    return default

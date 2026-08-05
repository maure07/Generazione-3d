"""Provider Tripo AI (image-to-3D).

Flusso del servizio:

1. ``POST /upload`` — carica l'immagine e ottiene un ``image_token``;
2. ``POST /task`` — crea un task ``image_to_model`` (o ``multiview_to_model``
   quando si forniscono più viste);
3. ``GET /task/{task_id}`` — polling fino a ``success``;
4. download del modello dall'URL restituito.

La chiave API si configura con ``PRINTREADY_TRIPO_API_KEY``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import get_settings
from .base import (
    GenerationRequest,
    GenerationResult,
    Image3DProvider,
    ProgressCallback,
    ProviderError,
)
from .http_client import download_file, guess_extension, request_json

logger = logging.getLogger(__name__)

#: Estensioni accettate dall'endpoint di upload.
MIME_BY_SUFFIX = {
    ".jpg": ("jpg", "image/jpeg"),
    ".jpeg": ("jpg", "image/jpeg"),
    ".png": ("png", "image/png"),
    ".webp": ("webp", "image/webp"),
}


class TripoProvider(Image3DProvider):
    """Client del servizio Tripo AI."""

    name = "tripo"
    label_it = "Tripo AI (cloud)"
    offline = False
    supports_parts = False
    supports_color = True
    max_images = 4

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.tripo_api_key
        self._base_url = settings.tripo_base_url.rstrip("/")
        self._timeout = settings.ai_timeout_s
        self._poll_interval = settings.ai_poll_interval_s

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(60.0, read=120.0),
            follow_redirects=True,
        )

    async def generate(
        self, request: GenerationRequest, progress: ProgressCallback | None = None
    ) -> GenerationResult:
        if not self.is_available():
            raise ProviderError(
                "Chiave API Tripo non configurata", provider=self.name, recoverable=False
            )

        started = time.time()
        async with self._client() as client:
            self._report(progress, 0.05, "Caricamento dell'immagine su Tripo")
            tokens = []
            for image_path in request.image_paths[: self.max_images]:
                tokens.append(await self._upload(client, image_path))

            self._report(progress, 0.15, "Creazione del task di generazione")
            task_id = await self._create_task(client, request, tokens)

            self._report(progress, 0.2, "Generazione in corso sul cloud Tripo")
            output = await self._wait_task(client, task_id, progress)

        model_url = self._extract_model_url(output)
        if not model_url:
            raise ProviderError(
                "Tripo non ha restituito alcun modello scaricabile", provider=self.name
            )

        destination = self._destination(task_id, guess_extension(model_url))
        self._report(progress, 0.92, "Scaricamento del modello generato")
        await download_file(model_url, destination, provider=self.name, timeout=self._timeout)

        self._report(progress, 1.0, "Modello Tripo pronto")
        return GenerationResult(
            mesh_path=destination,
            provider=self.name,
            duration_s=time.time() - started,
            raw_metadata={"task_id": task_id, "output": output},
        )

    # -- passi del protocollo ---------------------------------------------

    async def _upload(self, client: httpx.AsyncClient, image_path: Path) -> str:
        """Carica un'immagine e restituisce il token assegnato dal servizio."""
        suffix = image_path.suffix.lower()
        if suffix not in MIME_BY_SUFFIX:
            raise ProviderError(
                f"Formato immagine non supportato da Tripo: {suffix}",
                provider=self.name,
                recoverable=False,
            )
        fmt, mime = MIME_BY_SUFFIX[suffix]

        with image_path.open("rb") as handle:
            files = {"file": (image_path.name, handle.read(), mime)}

        payload = await request_json(
            client, "POST", "/upload", provider=self.name, files=files
        )
        token = (payload.get("data") or {}).get("image_token")
        if not token:
            raise ProviderError(
                f"Tripo non ha restituito il token dell'immagine: {payload}", provider=self.name
            )
        logger.debug("Immagine %s caricata su Tripo (%s)", image_path.name, fmt)
        return token

    async def _create_task(
        self, client: httpx.AsyncClient, request: GenerationRequest, tokens: list[str]
    ) -> str:
        """Crea il task di generazione e ne restituisce l'identificativo."""
        suffix = request.primary_image.suffix.lower()
        fmt = MIME_BY_SUFFIX.get(suffix, ("png", "image/png"))[0]

        if len(tokens) > 1:
            body: dict[str, Any] = {
                "type": "multiview_to_model",
                "files": [{"type": fmt, "file_token": token} for token in tokens],
            }
        else:
            body = {
                "type": "image_to_model",
                "file": {"type": fmt, "file_token": tokens[0]},
            }

        body.update(
            {
                "model_version": "v2.5-20250123",
                "face_limit": int(request.target_faces),
                "texture": bool(request.want_texture),
                "pbr": bool(request.want_texture),
            }
        )
        if request.prompt:
            body["prompt"] = request.prompt[:1000]
        if request.negative_prompt:
            body["negative_prompt"] = request.negative_prompt[:500]
        if request.seed is not None:
            body["model_seed"] = int(request.seed)

        payload = await request_json(client, "POST", "/task", provider=self.name, json=body)
        task_id = (payload.get("data") or {}).get("task_id")
        if not task_id:
            raise ProviderError(f"Tripo non ha creato il task: {payload}", provider=self.name)
        return str(task_id)

    async def _wait_task(
        self, client: httpx.AsyncClient, task_id: str, progress: ProgressCallback | None
    ) -> dict[str, Any]:
        """Attende il completamento del task, riportando l'avanzamento."""
        deadline = time.time() + self._timeout

        while time.time() < deadline:
            payload = await request_json(client, "GET", f"/task/{task_id}", provider=self.name)
            data = payload.get("data") or {}
            status = str(data.get("status", "")).lower()

            if status in {"success", "succeed", "completed"}:
                return data
            if status in {"failed", "cancelled", "banned", "expired"}:
                reason = data.get("message") or data.get("error") or status
                raise ProviderError(f"Generazione Tripo fallita: {reason}", provider=self.name)

            remote_progress = float(data.get("progress", 0) or 0) / 100.0
            self._report(
                progress,
                0.2 + 0.7 * remote_progress,
                f"Generazione Tripo al {int(remote_progress * 100)}%",
            )
            await asyncio.sleep(self._poll_interval)

        raise ProviderError(
            f"Tempo scaduto: Tripo non ha completato il task entro {self._timeout:.0f}s",
            provider=self.name,
        )

    def _extract_model_url(self, data: dict[str, Any]) -> str | None:
        """Cerca l'URL del modello fra i campi possibili della risposta."""
        output = data.get("output") or data.get("result") or {}
        for key in ("pbr_model", "model", "base_model", "rendered_model"):
            value = output.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                url = value.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    return url
        return None

    def _destination(self, task_id: str, extension: str) -> Path:
        settings = get_settings()
        folder = settings.cache_dir / "tripo"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{task_id}{extension}"

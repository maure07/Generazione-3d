"""Provider Meshy AI (image-to-3D).

Flusso del servizio:

1. ``POST /openapi/v1/image-to-3d`` con l'immagine come data URI base64;
2. polling su ``GET /openapi/v1/image-to-3d/{id}`` fino a ``SUCCEEDED``;
3. download del modello (GLB con texture) dagli ``model_urls``.

La chiave API si configura con ``PRINTREADY_MESHY_API_KEY``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
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

#: Dimensione massima ragionevole per un data URI (Meshy accetta ~10 MB).
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class MeshyProvider(Image3DProvider):
    """Client del servizio Meshy AI."""

    name = "meshy"
    label_it = "Meshy AI (cloud)"
    offline = False
    supports_parts = False
    supports_color = True
    max_images = 4

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.meshy_api_key
        self._base_url = settings.meshy_base_url.rstrip("/")
        self._timeout = settings.ai_timeout_s
        self._poll_interval = settings.ai_poll_interval_s

    def is_available(self) -> bool:
        return bool(self._api_key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, read=180.0),
            follow_redirects=True,
        )

    async def generate(
        self, request: GenerationRequest, progress: ProgressCallback | None = None
    ) -> GenerationResult:
        if not self.is_available():
            raise ProviderError(
                "Chiave API Meshy non configurata", provider=self.name, recoverable=False
            )

        started = time.time()
        self._report(progress, 0.05, "Preparazione dell'immagine per Meshy")
        data_uris = [self._to_data_uri(p) for p in request.image_paths[: self.max_images]]

        async with self._client() as client:
            self._report(progress, 0.12, "Invio della richiesta a Meshy")
            task_id, endpoint = await self._create_task(client, request, data_uris)

            self._report(progress, 0.2, "Generazione in corso sul cloud Meshy")
            data = await self._wait_task(client, endpoint, task_id, progress)

        model_url = self._extract_model_url(data)
        if not model_url:
            raise ProviderError(
                "Meshy non ha restituito alcun modello scaricabile", provider=self.name
            )

        destination = self._destination(task_id, guess_extension(model_url))
        self._report(progress, 0.92, "Scaricamento del modello generato")
        await download_file(model_url, destination, provider=self.name, timeout=self._timeout)

        texture_path = None
        texture_url = self._extract_texture_url(data)
        if texture_url:
            try:
                texture_path = await download_file(
                    texture_url,
                    destination.with_suffix(".png"),
                    provider=self.name,
                    timeout=self._timeout,
                )
            except ProviderError as exc:
                logger.warning("Texture Meshy non scaricata: %s", exc)

        self._report(progress, 1.0, "Modello Meshy pronto")
        return GenerationResult(
            mesh_path=destination,
            provider=self.name,
            texture_path=texture_path,
            duration_s=time.time() - started,
            raw_metadata={"task_id": task_id, "status": data.get("status")},
        )

    # -- passi del protocollo ---------------------------------------------

    def _to_data_uri(self, image_path: Path) -> str:
        """Converte l'immagine in data URI base64."""
        raw = image_path.read_bytes()
        if len(raw) > MAX_IMAGE_BYTES:
            raise ProviderError(
                f"Immagine troppo grande per Meshy ({len(raw) / 1e6:.1f} MB, massimo 10 MB)",
                provider=self.name,
                recoverable=False,
            )
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    async def _create_task(
        self, client: httpx.AsyncClient, request: GenerationRequest, data_uris: list[str]
    ) -> tuple[str, str]:
        """Crea il task e restituisce ``(task_id, endpoint)``."""
        if len(data_uris) > 1:
            endpoint = "/v1/multi-image-to-3d"
            body: dict[str, Any] = {"image_urls": data_uris}
        else:
            endpoint = "/v1/image-to-3d"
            body = {"image_url": data_uris[0]}

        body.update(
            {
                "ai_model": "meshy-5",
                "topology": "triangle",
                "target_polycount": int(request.target_faces),
                "should_texture": bool(request.want_texture),
                "should_remesh": True,
                "symmetry_mode": "auto",
            }
        )
        if request.prompt:
            body["texture_prompt"] = request.prompt[:600]

        payload = await request_json(client, "POST", endpoint, provider=self.name, json=body)
        task_id = payload.get("result") or payload.get("id")
        if not task_id:
            raise ProviderError(f"Meshy non ha creato il task: {payload}", provider=self.name)
        return str(task_id), endpoint

    async def _wait_task(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        task_id: str,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        """Attende il completamento del task riportando l'avanzamento."""
        deadline = time.time() + self._timeout

        while time.time() < deadline:
            data = await request_json(
                client, "GET", f"{endpoint}/{task_id}", provider=self.name
            )
            status = str(data.get("status", "")).upper()

            if status == "SUCCEEDED":
                return data
            if status in {"FAILED", "CANCELED", "CANCELLED", "EXPIRED"}:
                error = data.get("task_error") or {}
                reason = error.get("message") if isinstance(error, dict) else status
                raise ProviderError(f"Generazione Meshy fallita: {reason}", provider=self.name)

            remote_progress = float(data.get("progress", 0) or 0) / 100.0
            self._report(
                progress,
                0.2 + 0.7 * remote_progress,
                f"Generazione Meshy al {int(remote_progress * 100)}%",
            )
            await asyncio.sleep(self._poll_interval)

        raise ProviderError(
            f"Tempo scaduto: Meshy non ha completato il task entro {self._timeout:.0f}s",
            provider=self.name,
        )

    def _extract_model_url(self, data: dict[str, Any]) -> str | None:
        """Preferisce il GLB (contiene colori e texture), poi OBJ, poi altri."""
        urls = data.get("model_urls") or {}
        for key in ("glb", "obj", "fbx", "usdz", "ply", "stl"):
            value = urls.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        return None

    def _extract_texture_url(self, data: dict[str, Any]) -> str | None:
        textures = data.get("texture_urls") or []
        if isinstance(textures, list) and textures:
            first = textures[0]
            if isinstance(first, dict):
                for key in ("base_color", "diffuse", "albedo"):
                    value = first.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value
        return None

    def _destination(self, task_id: str, extension: str) -> Path:
        settings = get_settings()
        folder = settings.cache_dir / "meshy"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{task_id}{extension}"

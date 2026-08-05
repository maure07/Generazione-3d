"""Provider Hunyuan3D (istanza self-hosted o compatibile).

Hunyuan3D viene tipicamente eseguito in locale su GPU (Docker o server Gradio).
Non esiste un endpoint pubblico unico, quindi il client supporta i due schemi
più diffusi:

* **sincrono** — ``POST /generate`` restituisce direttamente il file del modello
  (``application/octet-stream``) oppure un JSON con il modello in base64;
* **asincrono** — ``POST /generate`` restituisce un ``uid``/``task_id`` e si
  interroga ``GET /status/{uid}`` fino al completamento.

L'endpoint si configura con ``PRINTREADY_HUNYUAN_ENDPOINT``
(es. ``http://127.0.0.1:8080``).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
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


class HunyuanProvider(Image3DProvider):
    """Client per un'istanza Hunyuan3D."""

    name = "hunyuan3d"
    label_it = "Hunyuan3D (locale o self-hosted)"
    offline = False  # richiede comunque un endpoint raggiungibile
    supports_parts = False
    supports_color = True
    max_images = 1

    def __init__(self) -> None:
        settings = get_settings()
        self._endpoint = settings.hunyuan_endpoint.rstrip("/")
        self._api_key = settings.hunyuan_api_key
        self._timeout = settings.ai_timeout_s
        self._poll_interval = settings.ai_poll_interval_s

    def is_available(self) -> bool:
        return bool(self._endpoint)

    def _client(self) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return httpx.AsyncClient(
            base_url=self._endpoint,
            headers=headers,
            timeout=httpx.Timeout(60.0, read=self._timeout),
            follow_redirects=True,
        )

    async def generate(
        self, request: GenerationRequest, progress: ProgressCallback | None = None
    ) -> GenerationResult:
        if not self.is_available():
            raise ProviderError(
                "Endpoint Hunyuan3D non configurato", provider=self.name, recoverable=False
            )

        started = time.time()
        self._report(progress, 0.05, "Codifica dell'immagine per Hunyuan3D")
        image_b64 = base64.b64encode(request.primary_image.read_bytes()).decode("ascii")

        body: dict[str, Any] = {
            "image": image_b64,
            "texture": bool(request.want_texture),
            "face_count": int(request.target_faces),
            "octree_resolution": 256,
            "num_inference_steps": 30,
            "guidance_scale": 5.0,
            "type": "glb",
        }
        if request.prompt:
            body["prompt"] = request.prompt[:800]
        if request.seed is not None:
            body["seed"] = int(request.seed)

        destination = self._destination(started)

        async with self._client() as client:
            self._report(progress, 0.15, "Invio della richiesta a Hunyuan3D")
            try:
                response = await client.post("/generate", json=body)
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"Hunyuan3D non raggiungibile all'indirizzo {self._endpoint}: {exc}",
                    provider=self.name,
                ) from exc

            if response.status_code >= 400:
                raise ProviderError(
                    f"Hunyuan3D ha rifiutato la richiesta (HTTP {response.status_code})",
                    provider=self.name,
                )

            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                # Risposta sincrona con il file binario del modello.
                destination.write_bytes(response.content)
                self._report(progress, 1.0, "Modello Hunyuan3D pronto")
                return GenerationResult(
                    mesh_path=destination,
                    provider=self.name,
                    duration_s=time.time() - started,
                    raw_metadata={"mode": "sincrono"},
                )

            payload = response.json()
            saved = self._save_inline_model(payload, destination)
            if saved is not None:
                self._report(progress, 1.0, "Modello Hunyuan3D pronto")
                return GenerationResult(
                    mesh_path=saved,
                    provider=self.name,
                    duration_s=time.time() - started,
                    raw_metadata={"mode": "json_inline"},
                )

            task_id = payload.get("uid") or payload.get("task_id") or payload.get("id")
            if not task_id:
                raise ProviderError(
                    f"Risposta Hunyuan3D non riconosciuta: {list(payload)[:6]}",
                    provider=self.name,
                )

            self._report(progress, 0.25, "Generazione in corso su Hunyuan3D")
            result = await self._wait_task(client, str(task_id), progress)

        model_url = result.get("model_url") or result.get("url")
        if isinstance(model_url, str) and model_url.startswith("http"):
            destination = destination.with_suffix(guess_extension(model_url))
            await download_file(model_url, destination, provider=self.name, timeout=self._timeout)
        else:
            saved = self._save_inline_model(result, destination)
            if saved is None:
                raise ProviderError(
                    "Hunyuan3D non ha restituito alcun modello", provider=self.name
                )
            destination = saved

        self._report(progress, 1.0, "Modello Hunyuan3D pronto")
        return GenerationResult(
            mesh_path=destination,
            provider=self.name,
            duration_s=time.time() - started,
            raw_metadata={"mode": "asincrono", "task_id": str(task_id)},
        )

    # -- passi del protocollo ---------------------------------------------

    async def _wait_task(
        self, client: httpx.AsyncClient, task_id: str, progress: ProgressCallback | None
    ) -> dict[str, Any]:
        """Polling dello stato fino al completamento."""
        deadline = time.time() + self._timeout

        while time.time() < deadline:
            data = await request_json(client, "GET", f"/status/{task_id}", provider=self.name)
            status = str(data.get("status", "")).lower()

            if status in {"completed", "success", "succeeded", "done"}:
                return data
            if status in {"error", "failed", "cancelled"}:
                raise ProviderError(
                    f"Generazione Hunyuan3D fallita: {data.get('message', status)}",
                    provider=self.name,
                )

            remote = float(data.get("progress", 0) or 0)
            remote = remote / 100.0 if remote > 1.0 else remote
            self._report(
                progress, 0.25 + 0.65 * remote, f"Hunyuan3D al {int(remote * 100)}%"
            )
            await asyncio.sleep(self._poll_interval)

        raise ProviderError(
            f"Tempo scaduto: Hunyuan3D non ha completato entro {self._timeout:.0f}s",
            provider=self.name,
        )

    def _save_inline_model(self, payload: dict[str, Any], destination: Path) -> Path | None:
        """Salva il modello se la risposta lo contiene già in base64."""
        for key in ("model_base64", "model", "mesh", "glb", "data"):
            value = payload.get(key)
            if not isinstance(value, str) or len(value) < 128:
                continue
            try:
                raw = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError):
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            return destination
        return None

    def _destination(self, timestamp: float) -> Path:
        settings = get_settings()
        folder = settings.cache_dir / "hunyuan"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"hunyuan_{int(timestamp * 1000)}.glb"

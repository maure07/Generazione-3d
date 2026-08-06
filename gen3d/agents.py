"""Orchestrazione dei sub-agenti locali via LM Studio.

Claude (questa sessione / Claude Code) resta l'HUB CENTRALE che guida
l'intero ciclo di vita del progetto: legge il codice, lo modifica, lancia
`main.py`, legge i log e decide i passi successivi. Questo modulo NON
duplica quel ruolo: espone soltanto dei client leggeri verso il server
OpenAI-compatible di LM Studio (http://localhost:1234/v1) per delegare,
in-process e senza mai lasciare la CLI, tre compiti a basso costo:

  - VisionAgent  -> Qwen2-VL-7B: analizza l'immagine 2D di input e propone
                    assi di simmetria, materiale/spessore suggerito, punti
                    critici (sbalzi, parti sottili).
  - CoderAgent   -> Qwen2.5-Coder-14B: genera piccoli frammenti Python
                    (es. un piano di taglio custom) quando l'euristica
                    geometrica di default non basta.
  - DebugAgent   -> Qwen2.5-Coder-14B: riceve un traceback + il codice che
                    lo ha causato e propone una patch, per il ciclo di
                    autodebug in background gestito da pipeline.py.

Tutti i sub-agenti sono opzionali: se LM Studio non e' raggiungibile, la
pipeline prosegue con le euristiche geometriche di default (vedi
pipeline.py) e logga soltanto un warning. L'orchestrazione del flusso
resta sempre e solo nel processo Python locale (nessuna piattaforma
esterna, nessuna finestra da cambiare).
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import LMStudioConfig

logger = logging.getLogger("gen3d.agents")


class LMStudioUnavailable(RuntimeError):
    """Sollevata quando il server locale di LM Studio non risponde."""


@dataclass
class LMStudioClient:
    """Wrapper minimale sull'API chat/completions OpenAI-compatible di LM Studio."""

    cfg: LMStudioConfig

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.cfg.base_url.rstrip('/')}/chat/completions"
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                json=payload,
                timeout=self.cfg.timeout_s,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise LMStudioUnavailable(
                f"Impossibile contattare LM Studio su {url}: {exc}"
            ) from exc

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post(payload)
        return data["choices"][0]["message"]["content"]

    def is_reachable(self) -> bool:
        try:
            requests.get(f"{self.cfg.base_url.rstrip('/')}/models", timeout=3)
            return True
        except requests.RequestException:
            return False


def _image_to_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    ext = path.suffix.lstrip(".").lower() or "png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{ext};base64,{b64}"


class VisionAgent:
    """Analizza l'immagine 2D di input con Qwen2-VL prima della generazione 3D."""

    SYSTEM_PROMPT = (
        "Sei un assistente tecnico per la stampa 3D. Analizza l'immagine e "
        "rispondi SOLO con un oggetto JSON con le chiavi: "
        "'subject' (stringa, cosa rappresenta l'oggetto), "
        "'symmetry_axis' (uno tra 'x','y','z','none'), "
        "'suggested_split_axis' (uno tra 'x','y','z', l'asse migliore per "
        "tagliare l'oggetto in piu' parti stampabili), "
        "'thin_features' (booleano, se ci sono parti sottili o sbalzi a rischio), "
        "'notes' (stringa breve, max 200 caratteri)."
    )

    def __init__(self, client: LMStudioClient):
        self.client = client

    def analyze(self, image_path: str | Path) -> dict[str, Any]:
        if not self.client.is_reachable():
            raise LMStudioUnavailable("LM Studio non raggiungibile per VisionAgent")
        data_url = _image_to_data_url(image_path)
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analizza questa immagine per la conversione in 3D stampabile."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        raw = self.client.chat(self.client.cfg.vision_model, messages, json_mode=True)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("VisionAgent: risposta non-JSON, uso fallback euristico. Output: %s", raw[:200])
            return {
                "subject": "unknown",
                "symmetry_axis": "none",
                "suggested_split_axis": "z",
                "thin_features": False,
                "notes": raw[:200],
            }


class CoderAgent:
    """Genera piccoli frammenti Python (piani di taglio custom, ecc.) su richiesta."""

    SYSTEM_PROMPT = (
        "Sei un ingegnere software Python esperto di trimesh e geometria 3D. "
        "Scrivi SOLO codice Python valido, senza spiegazioni, senza markdown fences, "
        "rispettando esattamente la firma di funzione richiesta dall'utente."
    )

    def __init__(self, client: LMStudioClient):
        self.client = client

    def generate_snippet(self, instruction: str, context_code: str = "") -> str:
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Contesto:\n```python\n{context_code}\n```\n\n"
                    f"Istruzione: {instruction}\n"
                    "Rispondi solo con codice Python, nient'altro."
                ),
            },
        ]
        code = self.client.chat(self.client.cfg.coder_model, messages)
        return _strip_markdown_fences(code)


class DebugAgent:
    """Analizza un traceback della pipeline e propone una patch mirata."""

    SYSTEM_PROMPT = (
        "Sei un debugger Python esperto in trimesh, numpy, shapely e geometria "
        "3D per la stampa. Ricevi il codice di una funzione e il traceback "
        "dell'errore che ha generato. Rispondi SOLO con un oggetto JSON con le "
        "chiavi: 'diagnosis' (causa probabile, breve), 'fixed_code' (la nuova "
        "versione COMPLETA della funzione, corretta), 'confidence' (0-1)."
    )

    def __init__(self, client: LMStudioClient):
        self.client = client

    def suggest_fix(self, function_source: str, traceback_text: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Codice della funzione:\n```python\n{function_source}\n```\n\n"
                    f"Traceback:\n```\n{traceback_text}\n```"
                ),
            },
        ]
        raw = self.client.chat(self.client.cfg.coder_model, messages, temperature=0.1, json_mode=True)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"diagnosis": "risposta non parsabile", "fixed_code": None, "confidence": 0.0}


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text

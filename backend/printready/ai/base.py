"""Interfaccia comune dei generatori immagine → 3D.

Ogni provider (Tripo, Meshy, Hunyuan3D, generatore locale) implementa
``Image3DProvider``. La pipeline non conosce i dettagli del singolo servizio:
riceve sempre un ``GenerationResult`` con il percorso della mesh grezza e i
metadati utili ai passi successivi (parti già separate, colori, texture).
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Errore di generazione con messaggio già localizzato in italiano."""

    def __init__(self, message_it: str, provider: str = "", recoverable: bool = True) -> None:
        super().__init__(message_it)
        self.message_it = message_it
        self.provider = provider
        self.recoverable = recoverable


@dataclass(slots=True)
class GenerationRequest:
    """Richiesta di generazione inviata a un provider."""

    image_paths: list[Path]
    prompt: str
    negative_prompt: str = ""
    #: Suggerimento sul numero di poligoni desiderato in uscita dal servizio.
    target_faces: int = 200_000
    #: Richiede la texture/colori se il provider li supporta.
    want_texture: bool = True
    #: Richiede la suddivisione in parti se il provider la supporta.
    want_parts: bool = True
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_image(self) -> Path:
        if not self.image_paths:
            raise ProviderError("Nessuna immagine fornita per la generazione")
        return self.image_paths[0]


@dataclass(slots=True)
class GenerationResult:
    """Esito di una generazione."""

    mesh_path: Path
    provider: str
    #: Parti già separate dal provider (nome → percorso file), se disponibili.
    part_paths: dict[str, Path] = field(default_factory=dict)
    texture_path: Path | None = None
    preview_path: Path | None = None
    duration_s: float = 0.0
    cost_credits: float = 0.0
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_parts(self) -> bool:
        return len(self.part_paths) > 1


@runtime_checkable
class ProgressCallback(Protocol):
    """Callback di avanzamento invocata dai provider durante il polling."""

    def __call__(self, progress: float, message_it: str) -> None:  # pragma: no cover - protocollo
        ...


class Image3DProvider(abc.ABC):
    """Classe base di un generatore immagine → mesh 3D."""

    #: Identificatore usato nelle impostazioni (``settings.ai_provider``).
    name: str = "base"
    #: Etichetta mostrata nell'interfaccia.
    label_it: str = "Provider generico"
    #: Il provider funziona senza connessione a Internet.
    offline: bool = False
    #: Il provider restituisce già i pezzi separati.
    supports_parts: bool = False
    #: Il provider restituisce colori o texture.
    supports_color: bool = False
    #: Numero massimo di immagini accettate in ingresso.
    max_images: int = 1

    @abc.abstractmethod
    def is_available(self) -> bool:
        """True se il provider è configurato e utilizzabile."""

    @abc.abstractmethod
    async def generate(
        self, request: GenerationRequest, progress: ProgressCallback | None = None
    ) -> GenerationResult:
        """Genera la mesh a partire da immagini e prompt.

        Raises:
            ProviderError: in caso di errore di configurazione, rete o servizio.
        """

    def describe(self) -> dict[str, Any]:
        """Descrizione serializzabile per l'interfaccia."""
        return {
            "name": self.name,
            "label_it": self.label_it,
            "available": self.is_available(),
            "offline": self.offline,
            "supports_parts": self.supports_parts,
            "supports_color": self.supports_color,
            "max_images": self.max_images,
        }

    # -- utilità condivise ------------------------------------------------

    @staticmethod
    def _report(progress: ProgressCallback | None, value: float, message_it: str) -> None:
        """Invoca il callback di avanzamento ignorando eventuali eccezioni."""
        if progress is None:
            return
        try:
            progress(float(max(0.0, min(1.0, value))), message_it)
        except Exception as exc:  # pragma: no cover - il callback non deve bloccare
            logger.debug("Callback di avanzamento fallito: %s", exc)

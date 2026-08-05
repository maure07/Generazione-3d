"""Registro dei provider AI con selezione automatica e riserva.

La modalità ``auto`` prova i provider in ordine di qualità attesa e ricade sul
generatore locale se nessun servizio cloud è configurato o raggiungibile:
l'applicazione produce **sempre** un modello, anche senza connessione.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import (
    GenerationRequest,
    GenerationResult,
    Image3DProvider,
    ProgressCallback,
    ProviderError,
)
from .hunyuan import HunyuanProvider
from .local import LocalProvider
from .meshy import MeshyProvider
from .tripo import TripoProvider

logger = logging.getLogger(__name__)

#: Ordine di preferenza in modalità automatica.
AUTO_ORDER = ["tripo", "meshy", "hunyuan3d", "local"]


class ProviderRegistry:
    """Contenitore dei provider disponibili."""

    def __init__(self) -> None:
        self._providers: dict[str, Image3DProvider] = {}
        self.register(TripoProvider())
        self.register(MeshyProvider())
        self.register(HunyuanProvider())
        self.register(LocalProvider())

    def register(self, provider: Image3DProvider) -> None:
        """Aggiunge (o sostituisce) un provider. Usato anche dai plugin."""
        self._providers[provider.name] = provider
        logger.debug("Provider AI registrato: %s", provider.name)

    def get(self, name: str) -> Image3DProvider | None:
        return self._providers.get(name)

    def available(self) -> list[Image3DProvider]:
        """Provider effettivamente utilizzabili, nell'ordine di preferenza."""
        ordered = [self._providers[n] for n in AUTO_ORDER if n in self._providers]
        extra = [p for name, p in self._providers.items() if name not in AUTO_ORDER]
        return [p for p in ordered + extra if p.is_available()]

    def describe_all(self) -> list[dict[str, Any]]:
        """Descrizione di tutti i provider per l'interfaccia."""
        return [p.describe() for p in self._providers.values()]

    def resolve(self, requested: str) -> list[Image3DProvider]:
        """Costruisce la catena di provider da provare.

        Args:
            requested: nome del provider o ``"auto"``.

        Returns:
            Lista ordinata: il primo è il preferito, gli altri sono riserve.

        Raises:
            ProviderError: se il provider richiesto non esiste o non è configurato.
        """
        if requested and requested != "auto":
            provider = self._providers.get(requested)
            if provider is None:
                raise ProviderError(
                    f"Provider AI sconosciuto: {requested}", recoverable=False
                )
            if not provider.is_available():
                raise ProviderError(
                    f"Il provider {provider.label_it} non è configurato: "
                    "inserire la chiave API nelle impostazioni",
                    provider=provider.name,
                    recoverable=False,
                )
            chain = [provider]
            # Il generatore locale resta sempre come rete di sicurezza.
            local = self._providers.get("local")
            if local is not None and local is not provider and local.is_available():
                chain.append(local)
            return chain

        chain = self.available()
        if not chain:
            raise ProviderError("Nessun provider AI disponibile", recoverable=False)
        return chain

    async def generate(
        self,
        requested: str,
        request: GenerationRequest,
        progress: ProgressCallback | None = None,
    ) -> GenerationResult:
        """Genera la mesh provando i provider in cascata.

        Un errore non recuperabile sul provider richiesto esplicitamente
        interrompe subito; in modalità ``auto`` si passa al successivo.
        """
        chain = self.resolve(requested)
        errors: list[str] = []

        for index, provider in enumerate(chain):
            is_last = index == len(chain) - 1
            try:
                logger.info("Generazione con il provider %s", provider.name)
                return await provider.generate(request, progress)
            except ProviderError as exc:
                errors.append(f"{provider.label_it}: {exc.message_it}")
                logger.warning("Provider %s fallito: %s", provider.name, exc.message_it)
                if is_last:
                    break
                if progress is not None:
                    progress(0.1, f"{provider.label_it} non disponibile, provo un'alternativa")
            except Exception as exc:  # pragma: no cover - errori imprevisti
                errors.append(f"{provider.label_it}: errore imprevisto ({exc})")
                logger.exception("Errore imprevisto nel provider %s", provider.name)
                if is_last:
                    break

        raise ProviderError(
            "Generazione non riuscita con nessun provider.\n" + "\n".join(errors)
        )


#: Registro condiviso a livello di applicazione.
registry = ProviderRegistry()

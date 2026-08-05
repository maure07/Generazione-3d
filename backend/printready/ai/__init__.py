"""Livello di intelligenza artificiale: generazione mesh e analisi semantica."""

from .base import (  # noqa: F401
    GenerationRequest,
    GenerationResult,
    Image3DProvider,
    ProviderError,
)
from .registry import ProviderRegistry, registry  # noqa: F401
from .semantic import PromptAnalysis, analyze_prompt, register_terms  # noqa: F401

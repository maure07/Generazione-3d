"""Operazioni booleane robuste.

Motore preferito: **manifold3d** (esatto, sempre manifold in uscita). Se non è
installato si ricade su Blender, se disponibile nel PATH. In assenza di
entrambi le funzioni degradano in modo controllato restituendo la mesh di
partenza e segnalando l'evento nel log: la pipeline resta eseguibile, ma gli
incastri non vengono sottratti.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import trimesh

from .io import is_empty

logger = logging.getLogger(__name__)

_ENGINE_CACHE: str | None = None


def engine_name() -> str:
    """Ritorna il motore booleano attivo: ``manifold``, ``blender`` o ``none``."""
    global _ENGINE_CACHE
    if _ENGINE_CACHE is not None:
        return _ENGINE_CACHE

    try:
        import manifold3d  # noqa: F401

        _ENGINE_CACHE = "manifold"
    except ImportError:
        try:
            from trimesh.interfaces import blender

            _ENGINE_CACHE = "blender" if blender.exists else "none"
        except Exception:  # pragma: no cover
            _ENGINE_CACHE = "none"

    logger.info("Motore booleano attivo: %s", _ENGINE_CACHE)
    return _ENGINE_CACHE


def _run(operation: str, meshes: Sequence[trimesh.Trimesh]) -> trimesh.Trimesh | None:
    """Esegue l'operazione booleana con il motore disponibile."""
    engine = engine_name()
    if engine == "none":
        logger.warning(
            "Nessun motore booleano disponibile: l'operazione '%s' viene saltata. "
            "Installare manifold3d per abilitarla.",
            operation,
        )
        return None

    func = getattr(trimesh.boolean, operation)
    try:
        result = func(list(meshes), engine=engine)
    except TypeError:
        # Alcune versioni di trimesh non accettano il parametro `engine`.
        result = func(list(meshes))
    except Exception as exc:
        logger.warning("Operazione booleana '%s' fallita: %s", operation, exc)
        return None

    if isinstance(result, list):  # pragma: no cover - dipende dalla versione
        result = trimesh.util.concatenate(result) if result else None
    if is_empty(result):
        return None
    return result


def boolean_union(meshes: Sequence[trimesh.Trimesh]) -> trimesh.Trimesh:
    """Unione booleana. In caso di fallimento concatena le mesh senza fondere."""
    valid = [m for m in meshes if not is_empty(m)]
    if not valid:
        raise ValueError("Nessuna mesh valida da unire")
    if len(valid) == 1:
        return valid[0].copy()

    result = _run("union", valid)
    if result is not None:
        return result
    logger.info("Unione booleana non disponibile: le mesh vengono concatenate")
    return trimesh.util.concatenate(valid)


def boolean_difference(
    target: trimesh.Trimesh, tools: Sequence[trimesh.Trimesh]
) -> trimesh.Trimesh:
    """Sottrae ``tools`` da ``target``.

    Se il motore booleano non è disponibile la mesh originale viene restituita
    invariata: preferiamo un pezzo senza alloggiamento a un pezzo corrotto.
    """
    valid = [m for m in tools if not is_empty(m)]
    if is_empty(target) or not valid:
        return target.copy()

    result = _run("difference", [target, *valid])
    if result is None:
        return target.copy()
    return result


def boolean_intersection(meshes: Sequence[trimesh.Trimesh]) -> trimesh.Trimesh | None:
    """Intersezione booleana; ``None`` se vuota o non calcolabile."""
    valid = [m for m in meshes if not is_empty(m)]
    if len(valid) < 2:
        return None
    return _run("intersection", valid)


def meshes_intersect(a: trimesh.Trimesh, b: trimesh.Trimesh, min_volume_mm3: float = 1e-4) -> bool:
    """Test rapido di compenetrazione fra due pezzi.

    Prima confronta gli AABB (economico), poi calcola l'intersezione booleana
    solo se necessario.
    """
    if is_empty(a) or is_empty(b):
        return False

    a_min, a_max = a.bounds
    b_min, b_max = b.bounds
    if (a_max < b_min).any() or (b_max < a_min).any():
        return False

    inter = boolean_intersection([a, b])
    if inter is None or is_empty(inter):
        return False
    return abs(float(inter.volume)) > min_volume_mm3

"""Export multi-formato (STL / OBJ / 3MF) dei pezzi finiti per lo slicer."""
from __future__ import annotations

import logging
from pathlib import Path

import trimesh

from .config import ExportConfig

logger = logging.getLogger("gen3d.exporter")

_SUPPORTED = {"stl", "obj", "3mf"}


def export_part(mesh: trimesh.Trimesh, name: str, cfg: ExportConfig) -> dict[str, str]:
    """Esporta una singola mesh in tutti i formati richiesti da config.

    Ritorna un dict {formato: path_assoluto}.
    """
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}
    for fmt in cfg.formats:
        fmt = fmt.lower().lstrip(".")
        if fmt not in _SUPPORTED:
            logger.warning("Formato di export non supportato, ignorato: %s", fmt)
            continue
        path = out_dir / f"{name}.{fmt}"
        _export_one(mesh, path, fmt, cfg)
        written[fmt] = str(path.resolve())
        logger.info("Esportato %s -> %s", name, path)
    return written


def _export_one(mesh: trimesh.Trimesh, path: Path, fmt: str, cfg: ExportConfig) -> None:
    if fmt == "stl":
        mesh.export(str(path), file_type="stl_ascii" if not cfg.binary_stl else "stl")
    elif fmt == "obj":
        mesh.export(str(path), file_type="obj")
    elif fmt == "3mf":
        mesh.export(str(path), file_type="3mf")


def export_all_parts(parts: dict[str, trimesh.Trimesh], cfg: ExportConfig) -> dict[str, dict[str, str]]:
    """Esporta un insieme di pezzi (nome -> mesh). Ritorna {nome: {formato: path}}."""
    results: dict[str, dict[str, str]] = {}
    for name, mesh in parts.items():
        results[name] = export_part(mesh, name, cfg)
    return results

"""Esportatori STL, OBJ e GLB, basati su trimesh.

* **STL** — lo standard universale degli slicer; nessun colore, un solido per file.
* **OBJ** — testuale, con file ``.mtl`` per i materiali; utile per il riuso in
  altri software 3D.
* **GLB** — binario glTF, conserva colori e gerarchia: ottimo per l'anteprima e
  per la condivisione.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import trimesh

from ..domain.enums import ExportFormat
from ..domain.models import ExportedFile
from ..mesh.io import is_empty
from .base import ExportError, Exporter, ExportItem, safe_filename

logger = logging.getLogger(__name__)


class STLExporter(Exporter):
    """Esportazione in STL binario: un file per pezzo, più uno combinato."""

    format = ExportFormat.STL
    supports_color = False
    supports_multi_object = False

    def export(
        self, items: list[ExportItem], destination: Path, combined: bool = False
    ) -> list[ExportedFile]:
        destination = self._prepare(destination)
        produced: list[ExportedFile] = []
        used: set[str] = set()

        for item in items:
            if is_empty(item.mesh):
                logger.warning("Pezzo «%s» vuoto: escluso dall'export STL", item.name)
                continue
            filename = _unique(safe_filename(item.name), used) + ".stl"
            path = destination / filename
            try:
                item.mesh.export(path, file_type="stl")
            except Exception as exc:
                raise ExportError(f"Esportazione STL di «{item.name}» fallita: {exc}") from exc
            file = self._describe(path, item.part_id, combined=False)
            file.slicer_hint_it = "Importare il file e orientare il pezzo sul piatto"
            produced.append(file)

        if combined and len(items) > 1:
            merged = _merge([i.mesh for i in items])
            if merged is not None:
                path = destination / "modello_completo.stl"
                merged.export(path, file_type="stl")
                produced.append(self._describe(path, None, combined=True))

        return produced


class OBJExporter(Exporter):
    """Esportazione in OBJ con materiali per pezzo."""

    format = ExportFormat.OBJ
    supports_color = True
    supports_multi_object = True

    def export(
        self, items: list[ExportItem], destination: Path, combined: bool = False
    ) -> list[ExportedFile]:
        destination = self._prepare(destination)
        produced: list[ExportedFile] = []
        used: set[str] = set()

        for item in items:
            if is_empty(item.mesh):
                continue
            filename = _unique(safe_filename(item.name), used) + ".obj"
            path = destination / filename
            mesh = _with_color(item.mesh, item.color_hex)
            try:
                mesh.export(path, file_type="obj")
            except Exception as exc:
                raise ExportError(f"Esportazione OBJ di «{item.name}» fallita: {exc}") from exc
            produced.append(self._describe(path, item.part_id, combined=False))

        if combined and len(items) > 1:
            scene = trimesh.Scene()
            for item in items:
                if not is_empty(item.mesh):
                    scene.add_geometry(
                        _with_color(item.mesh, item.color_hex),
                        geom_name=safe_filename(item.name),
                    )
            path = destination / "modello_completo.obj"
            try:
                scene.export(path, file_type="obj")
                produced.append(self._describe(path, None, combined=True))
            except Exception as exc:  # pragma: no cover
                logger.warning("Export OBJ combinato non riuscito: %s", exc)

        return produced


class GLBExporter(Exporter):
    """Esportazione in GLB: un unico file con tutti i pezzi e i loro colori."""

    format = ExportFormat.GLB
    supports_color = True
    supports_multi_object = True

    def export(
        self, items: list[ExportItem], destination: Path, combined: bool = True
    ) -> list[ExportedFile]:
        destination = self._prepare(destination)
        scene = trimesh.Scene()
        used: set[str] = set()

        for item in items:
            if is_empty(item.mesh):
                continue
            name = _unique(safe_filename(item.name), used)
            scene.add_geometry(_with_color(item.mesh, item.color_hex), geom_name=name)

        if not scene.geometry:
            raise ExportError("Nessun pezzo valido da esportare in GLB")

        path = destination / "modello_completo.glb"
        try:
            path.write_bytes(scene.export(file_type="glb"))
        except Exception as exc:
            raise ExportError(f"Esportazione GLB fallita: {exc}") from exc

        file = self._describe(path, None, combined=True)
        file.slicer_hint_it = "Formato ideale per l'anteprima e la condivisione, non per lo slicing"
        return [file]


# ---------------------------------------------------------------------------
# Utilità
# ---------------------------------------------------------------------------


def _unique(name: str, used: set[str]) -> str:
    """Rende univoco un nome di file all'interno della stessa cartella."""
    candidate = name
    counter = 2
    while candidate in used:
        candidate = f"{name}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _with_color(mesh: trimesh.Trimesh, color_hex: str | None) -> trimesh.Trimesh:
    """Applica un colore uniforme alla mesh, se richiesto."""
    if not color_hex:
        return mesh
    try:
        value = color_hex.lstrip("#")
        rgb = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, IndexError):
        return mesh

    copy = mesh.copy()
    copy.visual = trimesh.visual.ColorVisuals(
        mesh=copy, face_colors=np.tile(np.array([*rgb, 255], dtype=np.uint8), (len(copy.faces), 1))
    )
    return copy


def _merge(meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh | None:
    """Concatena le mesh non vuote."""
    valid = [m for m in meshes if not is_empty(m)]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    return trimesh.util.concatenate(valid)

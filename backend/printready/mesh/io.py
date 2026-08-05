"""Caricamento e salvataggio di mesh e scene.

Wrapper attorno a trimesh che normalizza i risultati: qualunque sia il formato
in ingresso, l'applicazione lavora sempre con ``trimesh.Trimesh`` (mesh singola)
oppure ``trimesh.Scene`` (insieme di pezzi nominati).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import trimesh

logger = logging.getLogger(__name__)

#: Estensioni accettate in ingresso.
SUPPORTED_INPUT = {".stl", ".obj", ".ply", ".glb", ".gltf", ".3mf", ".off", ".dae"}


class MeshLoadError(RuntimeError):
    """Errore di caricamento con messaggio già localizzato in italiano."""


def load_scene(path: str | Path) -> trimesh.Scene:
    """Carica un file 3D preservando la suddivisione in pezzi.

    Args:
        path: percorso del file.

    Returns:
        Una ``trimesh.Scene`` con almeno una geometria.

    Raises:
        MeshLoadError: se il file è assente, vuoto o in un formato non supportato.
    """
    path = Path(path)
    if not path.exists():
        raise MeshLoadError(f"File non trovato: {path}")
    if path.suffix.lower() not in SUPPORTED_INPUT:
        raise MeshLoadError(f"Formato non supportato: {path.suffix}")

    try:
        loaded = trimesh.load(path, force="scene", process=False)
    except Exception as exc:  # pragma: no cover - dipende dal file
        raise MeshLoadError(f"Impossibile leggere il file 3D: {exc}") from exc

    if isinstance(loaded, trimesh.Trimesh):
        scene = trimesh.Scene()
        scene.add_geometry(loaded, geom_name=path.stem)
        return scene
    if not isinstance(loaded, trimesh.Scene) or len(loaded.geometry) == 0:
        raise MeshLoadError("Il file non contiene geometrie valide")
    return loaded


def load_mesh(path: str | Path) -> trimesh.Trimesh:
    """Carica un file 3D come mesh unica (le parti vengono concatenate)."""
    scene = load_scene(path)
    return scene_to_mesh(scene)


def scene_to_mesh(scene: trimesh.Scene) -> trimesh.Trimesh:
    """Concatena tutte le geometrie di una scena in un'unica mesh.

    Le trasformazioni dei nodi vengono applicate, così la mesh risultante è
    nello spazio del mondo.
    """
    meshes: list[trimesh.Trimesh] = []
    for name, geom in scene.geometry.items():
        if not isinstance(geom, trimesh.Trimesh) or geom.faces.size == 0:
            continue
        copy = geom.copy()
        try:
            transform = scene.graph.get(name)[0]
            copy.apply_transform(transform)
        except Exception:  # pragma: no cover - grafo non standard
            logger.debug("Nessuna trasformazione per la geometria %s", name)
        meshes.append(copy)

    if not meshes:
        raise MeshLoadError("La scena non contiene mesh triangolari")
    if len(meshes) == 1:
        return meshes[0]
    return trimesh.util.concatenate(meshes)


def save_mesh(mesh: trimesh.Trimesh, path: str | Path) -> Path:
    """Salva una mesh creando le cartelle intermedie."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)
    return path


def ensure_trimesh(obj: trimesh.Trimesh | trimesh.Scene) -> trimesh.Trimesh:
    """Normalizza mesh/scena a ``Trimesh``."""
    if isinstance(obj, trimesh.Scene):
        return scene_to_mesh(obj)
    return obj


def empty_mesh() -> trimesh.Trimesh:
    """Mesh vuota valida, usata come valore neutro nelle operazioni."""
    return trimesh.Trimesh(
        vertices=np.zeros((0, 3), dtype=np.float64),
        faces=np.zeros((0, 3), dtype=np.int64),
        process=False,
    )


def is_empty(mesh: trimesh.Trimesh | None) -> bool:
    """True se la mesh è assente o priva di facce."""
    return mesh is None or len(mesh.faces) == 0

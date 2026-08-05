"""Configurazione comune dei test.

Ogni sessione di test lavora in una cartella dati temporanea: i test non
devono mai toccare i progetti reali dell'utente.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import trimesh

# La cartella dati va impostata prima di importare qualunque modulo che
# legga la configurazione, perché le impostazioni sono memorizzate in cache.
_TEMP_DIR = tempfile.mkdtemp(prefix="printready_test_")

import os  # noqa: E402

os.environ.setdefault("PRINTREADY_DATA_DIR", _TEMP_DIR)
os.environ.setdefault("PRINTREADY_LOG_LEVEL", "WARNING")


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """Cartella dati temporanea della sessione di test."""
    return Path(_TEMP_DIR)


@pytest.fixture
def cubo() -> trimesh.Trimesh:
    """Cubo di 20 mm, solido valido di riferimento."""
    return trimesh.creation.box(extents=[20.0, 20.0, 20.0])


@pytest.fixture
def sfera() -> trimesh.Trimesh:
    """Sfera di raggio 15 mm, mesh densa e regolare."""
    return trimesh.creation.icosphere(subdivisions=3, radius=15.0)


@pytest.fixture
def mesh_rotta(sfera: trimesh.Trimesh) -> trimesh.Trimesh:
    """Sfera con facce mancanti e duplicate: caso tipico da riparare."""
    import numpy as np

    faces = np.vstack([sfera.faces[:-30], sfera.faces[:15]])
    return trimesh.Trimesh(vertices=sfera.vertices, faces=faces, process=False)


@pytest.fixture
def figura() -> trimesh.Trimesh:
    """Figura umanoide sintetica in stile Funko Pop.

    I volumi si compenetrano di proposito: è così che si comporta un modello
    reale, e serve perché l'unione booleana produca un solido unico.
    """
    import trimesh.boolean as boolean

    parti = []

    base = trimesh.creation.cylinder(radius=28, height=8)
    base.apply_translation([0, 0, 4])
    parti.append(base)

    for x in (-11, 11):
        scarpa = trimesh.creation.box(extents=[14, 20, 10])
        scarpa.apply_translation([x, 2, 12])
        parti.append(scarpa)

        gamba = trimesh.creation.cylinder(radius=7, height=30)
        gamba.apply_translation([x, 0, 30])
        parti.append(gamba)

    corpo = trimesh.creation.box(extents=[36, 20, 34])
    corpo.apply_translation([0, 0, 58])
    parti.append(corpo)

    for x in (-23, 23):
        braccio = trimesh.creation.cylinder(radius=6, height=30)
        braccio.apply_translation([x, 0, 58])
        parti.append(braccio)

    collo = trimesh.creation.cylinder(radius=7, height=12)
    collo.apply_translation([0, 0, 76])
    parti.append(collo)

    testa = trimesh.creation.icosphere(subdivisions=3, radius=26)
    testa.apply_translation([0, 0, 104])
    parti.append(testa)

    return boolean.union(parti)

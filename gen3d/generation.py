"""Generazione della mesh 3D grezza a partire dall'immagine 2D.

Tre modelli locali vengono orchestrati come backend intercambiabili:

  - InstantMesh (FlexiCubes) -> geometria di superficie e spigoli, e' il
    motore principale.
  - TripoSR -> volumetria di base, molto veloce, usato come prior
    stabilizzante (aiuta a riempire zone dove InstantMesh e' incerto).
  - LGM     -> ricostruzione multi-vista, buono per le zone d'ombra /
    il retro dell'oggetto che l'immagine singola non mostra.

Ogni modello e' un repository esterno con il proprio ambiente/CLI, quindi
l'integrazione avviene come ADAPTER A SUBPROCESS: ogni backend lancia lo
script di inferenza ufficiale del repo clonato in `third_party/`, passando
il checkpoint locale gia' scaricato, e legge il file mesh prodotto in
output. Questo evita di dover reimplementare/importare tre codebase PyTorch
con dipendenze spesso incompatibili tra loro nello stesso processo.

I tre output vengono poi fusi in un'unica mesh tramite voto pesato su una
griglia voxel comune (vedi `fuse_meshes`): e' l'operazione che permette di
"combinare la potenza dei tre modelli" in una singola geometria migliore
della somma delle parti.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from .config import AppConfig, GenerationConfig, ModelPaths

logger = logging.getLogger("gen3d.generation")

_MESH_EXTENSIONS = (".obj", ".glb", ".gltf", ".ply", ".stl")

# Comandi di default per gli script ufficiali dei tre repository. Sono
# TEMPLATE pensati per i layout upstream piu' comuni: verificali contro il
# README del repo che hai clonato in third_party/ e correggili in
# config.yaml (sezione `models.commands`) se la tua versione differisce --
# ogni progetto di ricerca cambia CLI abbastanza spesso.
DEFAULT_COMMANDS = {
    "instantmesh": (
        "{python} run.py configs/instant-mesh-large.yaml {image} "
        "--output_path {output_dir} --save_video False --export_texmap False"
    ),
    "triposr": (
        "{python} run.py {image} --output-dir {output_dir} "
        "--pretrained-model-name-or-path {ckpt_dir} --model-save-format obj"
    ),
    "lgm": (
        "{python} infer.py big --resume {ckpt} --test_path {image} "
        "--workspace {output_dir}"
    ),
}


class BackendExecutionError(RuntimeError):
    """Lo script di inferenza esterno e' fallito o non ha prodotto una mesh."""


@dataclass
class GeneratedMesh:
    backend: str
    mesh: trimesh.Trimesh
    weight: float
    raw_path: Path


class SubprocessBackend:
    """Adapter generico: lancia uno script esterno e recupera la mesh prodotta."""

    def __init__(self, name: str, repo_dir: str, command_template: str, python_exe: str, timeout_s: int = 1800):
        self.name = name
        self.repo_dir = Path(repo_dir)
        self.command_template = command_template
        self.python_exe = python_exe
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        return self.repo_dir.exists() and (self.repo_dir / "run.py").exists() or self.repo_dir.exists()

    def run(self, image_path: str, output_dir: Path, ckpt_path: str) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd_str = self.command_template.format(
            python=self.python_exe,
            image=image_path,
            output_dir=str(output_dir),
            ckpt=ckpt_path,
            ckpt_dir=str(Path(ckpt_path).parent),
            repo=str(self.repo_dir),
        )
        cmd = shlex.split(cmd_str, posix=(not _is_windows()))
        logger.info("[%s] eseguo: %s (cwd=%s)", self.name, cmd_str, self.repo_dir)

        before = _snapshot_mtimes(output_dir)
        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise BackendExecutionError(f"[{self.name}] timeout dopo {self.timeout_s}s") from exc
        except FileNotFoundError as exc:
            raise BackendExecutionError(
                f"[{self.name}] eseguibile o repo non trovato ({self.repo_dir}): {exc}"
            ) from exc

        elapsed = time.time() - start
        if proc.returncode != 0:
            raise BackendExecutionError(
                f"[{self.name}] uscito con codice {proc.returncode} dopo {elapsed:.1f}s\n"
                f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
            )

        mesh_path = _find_newest_mesh(output_dir, after_mtimes=before)
        if mesh_path is None:
            raise BackendExecutionError(
                f"[{self.name}] terminato senza errori ma nessun file mesh trovato in {output_dir}"
            )
        logger.info("[%s] completato in %.1fs -> %s", self.name, elapsed, mesh_path)
        return mesh_path


def _is_windows() -> bool:
    import platform

    return platform.system().lower() == "windows"


def _snapshot_mtimes(directory: Path) -> dict[str, float]:
    if not directory.exists():
        return {}
    return {str(p): p.stat().st_mtime for p in directory.rglob("*") if p.is_file()}


def _find_newest_mesh(directory: Path, after_mtimes: dict[str, float]) -> Path | None:
    candidates = []
    for p in directory.rglob("*"):
        if p.is_file() and p.suffix.lower() in _MESH_EXTENSIONS:
            prev_mtime = after_mtimes.get(str(p))
            if prev_mtime is None or p.stat().st_mtime > prev_mtime:
                candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _load_and_normalize(path: Path, target_size_mm: float) -> trimesh.Trimesh:
    loaded = trimesh.load(str(path), force="mesh")
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate([g for g in loaded.geometry.values()])
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise BackendExecutionError(f"File prodotto non e' una mesh valida: {path}")
    extents = loaded.extents
    longest = float(np.max(extents)) if extents is not None and np.max(extents) > 0 else 1.0
    loaded.apply_scale(target_size_mm / longest)
    loaded.apply_translation(-loaded.centroid)
    return loaded


def build_backends(cfg: AppConfig) -> dict[str, SubprocessBackend]:
    models: ModelPaths = cfg.models
    commands = dict(DEFAULT_COMMANDS)
    return {
        "instantmesh": SubprocessBackend(
            "instantmesh", models.instantmesh_repo, commands["instantmesh"], models.python_executable
        ),
        "triposr": SubprocessBackend(
            "triposr", models.triposr_repo, commands["triposr"], models.python_executable
        ),
        "lgm": SubprocessBackend("lgm", models.lgm_repo, commands["lgm"], models.python_executable),
    }


def _ckpt_for(name: str, models: ModelPaths) -> str:
    return {
        "instantmesh": models.instantmesh_ckpt,
        "triposr": models.triposr_ckpt,
        "lgm": models.lgm_ckpt,
    }[name]


def generate_candidates(
    image_path: str,
    cfg: AppConfig,
    work_dir: Path,
) -> list[GeneratedMesh]:
    """Esegue ogni backend configurato e ritorna le mesh grezze normalizzate."""
    backends = build_backends(cfg)
    results: list[GeneratedMesh] = []
    for name in cfg.generation.backends:
        backend = backends.get(name)
        if backend is None:
            logger.warning("Backend sconosciuto in config, ignorato: %s", name)
            continue
        if not backend.is_available():
            logger.warning(
                "[%s] repository non trovato in %s: salto questo backend. "
                "Clona il repo ufficiale in third_party/ per abilitarlo.",
                name,
                backend.repo_dir,
            )
            continue
        try:
            mesh_path = backend.run(
                image_path, work_dir / name, _ckpt_for(name, cfg.models)
            )
            mesh = _load_and_normalize(mesh_path, cfg.generation.target_size_mm)
            weight = cfg.generation.fusion_weights.get(name, 1.0)
            results.append(GeneratedMesh(backend=name, mesh=mesh, weight=weight, raw_path=mesh_path))
        except BackendExecutionError as exc:
            logger.error("%s", exc)
    if not results:
        raise BackendExecutionError(
            "Nessun backend di generazione ha prodotto una mesh valida. "
            "Verifica i percorsi in config.yaml (models.*) e che i repository "
            "third_party siano clonati e funzionanti."
        )
    return results


def fuse_meshes(candidates: list[GeneratedMesh], cfg: GenerationConfig) -> trimesh.Trimesh:
    """Fonde piu' mesh candidate in una sola tramite voto pesato su griglia voxel.

    Ogni mesh viene voxelizzata e riempita (occupancy binaria); i voxel
    vengono sommati pesati per la confidenza del backend; un voxel finale e'
    "pieno" se la somma dei pesi supera `fusion_vote_threshold`. La mesh
    fusa e' ricostruita con marching cubes: il risultato e' sempre una
    superficie chiusa (nessun ulteriore repair e' in teoria necessario, ma
    la pipeline la fa comunque passare da mesh_tools per sicurezza).
    """
    if len(candidates) == 1:
        return candidates[0].mesh

    pitch = cfg.voxel_pitch_mm
    pad = pitch * 3
    all_bounds = np.array([c.mesh.bounds for c in candidates])
    global_min = all_bounds[:, 0, :].min(axis=0) - pad
    global_max = all_bounds[:, 1, :].max(axis=0) + pad
    grid_shape = np.ceil((global_max - global_min) / pitch).astype(int) + 1

    accumulator = np.zeros(tuple(grid_shape), dtype=np.float32)

    for cand in candidates:
        voxel_grid = cand.mesh.voxelized(pitch=pitch).fill()
        matrix = voxel_grid.matrix.astype(bool)
        origin = np.array(voxel_grid.transform[:3, 3])
        offset = np.round((origin - global_min) / pitch).astype(int)

        lo = np.clip(offset, 0, grid_shape)
        hi = np.clip(offset + np.array(matrix.shape), 0, grid_shape)
        if np.any(hi <= lo):
            logger.warning("[%s] voxel grid fuori dai limiti globali, ignorato nella fusione", cand.backend)
            continue
        src_lo = lo - offset
        src_hi = hi - offset
        accumulator[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]] += (
            cand.weight * matrix[src_lo[0]:src_hi[0], src_lo[1]:src_hi[1], src_lo[2]:src_hi[2]]
        )

    occupied = accumulator >= cfg.fusion_vote_threshold
    if not occupied.any():
        logger.warning("Fusione voxel: nessun voxel supera la soglia, fallback al backend con peso maggiore")
        return max(candidates, key=lambda c: c.weight).mesh

    fused = trimesh.voxel.ops.matrix_to_marching_cubes(occupied, pitch=pitch)
    fused.apply_translation(global_min)
    return fused


def generate_3d(image_path: str, cfg: AppConfig, work_dir: Path) -> trimesh.Trimesh:
    """Punto d'ingresso: immagine -> mesh grezza fusa (non ancora ripulita)."""
    candidates = generate_candidates(image_path, cfg, work_dir)
    logger.info("Backend riusciti: %s", [c.backend for c in candidates])
    return fuse_meshes(candidates, cfg.generation)

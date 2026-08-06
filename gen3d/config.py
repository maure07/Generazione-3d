"""Configurazione centrale della pipeline 2D -> 3D.

Un'unica sorgente di verita' per: percorsi dei checkpoint AI locali,
endpoint di LM Studio, parametri geometrici (tolleranze, dimensioni piano
di stampa) e preferenze di esecuzione. Tutto e' sovrascrivibile da un file
YAML esterno (config.yaml) o da variabili d'ambiente, cosi' il codice non
va mai toccato per adattare la pipeline a una macchina diversa.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class LMStudioConfig:
    """Endpoint OpenAI-compatible esposto da LM Studio (server locale)."""

    base_url: str = "http://localhost:1234/v1"
    api_key: str = "lm-studio"  # LM Studio ignora la chiave ma la libreria openai la richiede
    vision_model: str = "qwen2-vl-7b-instruct"
    coder_model: str = "qwen2.5-coder-14b-instruct"
    fast_model: str = "qwen2.5-coder-7b-instruct"
    timeout_s: int = 120
    temperature: float = 0.2


@dataclass
class ModelPaths:
    """Percorsi locali ai pesi/eseguibili gia' scaricati sul PC."""

    instantmesh_ckpt: str = r"C:\models\instantmesh\instant_mesh_large.ckpt"
    triposr_ckpt: str = r"C:\models\triposr\model.ckpt"
    lgm_ckpt: str = r"C:\models\lgm\model_fp16.safetensors"

    # Percorsi ai repository clonati (third_party) che contengono gli script
    # di inferenza ufficiali. Il pattern e' "adapter a subprocess": ogni
    # backend viene invocato come processo esterno con un comando
    # personalizzabile (vedi generation.py / config.yaml).
    instantmesh_repo: str = str(REPO_ROOT / "third_party" / "InstantMesh")
    triposr_repo: str = str(REPO_ROOT / "third_party" / "TripoSR")
    lgm_repo: str = str(REPO_ROOT / "third_party" / "LGM")

    python_executable: str = _env("GEN3D_PYTHON", "python")


@dataclass
class GenerationConfig:
    """Parametri di generazione mesh e fusione dell'ensemble dei 3 modelli."""

    backends: list[str] = field(default_factory=lambda: ["instantmesh", "triposr", "lgm"])
    # Pesi usati nella fusione voxel: InstantMesh domina i dettagli di
    # superficie/spigoli, LGM riempie le zone d'ombra sul retro, TripoSR
    # fa da prior veloce e stabilizzante.
    fusion_weights: dict[str, float] = field(
        default_factory=lambda: {"instantmesh": 1.2, "triposr": 0.8, "lgm": 1.0}
    )
    voxel_pitch_mm: float = 1.0
    fusion_vote_threshold: float = 1.0  # soglia (somma pesi) per considerare un voxel "pieno"
    target_size_mm: float = 100.0  # dimensione massima del lato dopo normalizzazione
    remove_background: bool = True
    device: str = _env("GEN3D_DEVICE", "cuda")


@dataclass
class MeshCleanupConfig:
    fill_holes: bool = True
    max_hole_edges: int = 200
    remove_small_components_ratio: float = 0.02  # elimina componenti < 2% del volume max
    target_faces: int | None = None  # None = nessuna decimazione
    smooth_iterations: int = 0  # 0 = nessuno smoothing (preserva spigoli)
    fix_normals: bool = True
    merge_vertex_tolerance_mm: float = 0.01


@dataclass
class CutterConfig:
    """Parametri per taglio + connettori maschio/femmina (perno/sede)."""

    pin_diameter_mm: float = 6.0
    pin_length_mm: float = 12.0
    tolerance_mm: float = 0.2  # gioco radiale sede rispetto al perno
    embed_ratio: float = 0.45  # frazione del perno annegata (unita) nella parte "maschio"
    socket_extra_depth_mm: float = 0.4  # profondita' extra della sede per evitare interferenze
    n_pins: int = 2
    min_pin_edge_distance_mm: float = 3.0  # distanza minima perno dal bordo del taglio
    boolean_engines: list[str] = field(default_factory=lambda: ["manifold", "blender", "scad"])
    max_part_bbox_mm: tuple[float, float, float] = (220.0, 220.0, 250.0)  # piano di stampa


@dataclass
class ExportConfig:
    formats: list[str] = field(default_factory=lambda: ["stl", "obj", "3mf"])
    output_dir: str = str(REPO_ROOT / "output")
    binary_stl: bool = True


@dataclass
class OrchestratorConfig:
    max_autodebug_retries: int = 3
    log_dir: str = str(REPO_ROOT / "logs")
    use_vision_agent: bool = True
    use_autodebug: bool = True


@dataclass
class AppConfig:
    lmstudio: LMStudioConfig = field(default_factory=LMStudioConfig)
    models: ModelPaths = field(default_factory=ModelPaths)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    cleanup: MeshCleanupConfig = field(default_factory=MeshCleanupConfig)
    cutter: CutterConfig = field(default_factory=CutterConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        """Carica la config di default e la sovrascrive con config.yaml (se esiste)."""
        cfg = cls()
        yaml_path = Path(path) if path else REPO_ROOT / "config.yaml"
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as fh:
                overrides = yaml.safe_load(fh) or {}
            _apply_overrides(cfg, overrides)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _apply_overrides(cfg: AppConfig, overrides: dict[str, Any]) -> None:
    for section_name, section_values in overrides.items():
        if not hasattr(cfg, section_name) or not isinstance(section_values, dict):
            continue
        section = getattr(cfg, section_name)
        for key, value in section_values.items():
            if hasattr(section, key):
                setattr(section, key, value)


if __name__ == "__main__":
    import json

    print(json.dumps(AppConfig.load().to_dict(), indent=2, default=str))

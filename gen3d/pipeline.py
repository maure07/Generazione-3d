"""Orchestratore unico: immagine 2D -> pezzi 3D stampabili con connettori.

Questo e' l'UNICO punto da cui parte l'intero flusso (invocato da
`main.py`, che espone un solo comando da terminale). Nessuna finestra o
piattaforma separata: il flusso vive in un processo Python locale che
- consulta LM Studio in locale solo per i due task delegabili (visione,
  debug), mantenendo il controllo del flusso, dell'esecuzione e del
  ciclo di errori qui dentro;
- lancia i tre backend di generazione 3D come sub-processi locali;
- esegue la geometria (pulizia, taglio, connettori, export) in-process
  con trimesh/numpy/shapely.

Ciclo di autodebug: se una fase della pipeline solleva un'eccezione non
transitoria, viene interrogato il DebugAgent (Qwen2.5-Coder via LM
Studio) per una diagnosi e una patch suggerita. La patch NON viene mai
eseguita automaticamente (un modello locale da 14B che scrive codice
eseguito a occhi chiusi e' un rischio di sicurezza, non una comodita'):
viene scritta come report in `logs/autodebug/` pronto per essere
revisionato e applicato dall'agente/sviluppatore che guida la sessione.
"""
from __future__ import annotations

import inspect
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

import trimesh

from .config import AppConfig
from . import mesh_tools, cutter, exporter, generation
from .agents import LMStudioClient, VisionAgent, DebugAgent, LMStudioUnavailable

logger = logging.getLogger("gen3d.pipeline")

T = TypeVar("T")


@dataclass
class PipelineResult:
    image_path: str
    vision_analysis: dict[str, Any] | None
    n_parts: int
    split_axis: str | None
    printability_warnings: dict[str, list[str]]
    exported_files: dict[str, dict[str, str]]
    elapsed_s: float


def setup_logging(log_dir: str) -> Path:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log"
    root = logging.getLogger("gen3d")
    root.setLevel(logging.INFO)
    root.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(fh)
    root.addHandler(ch)
    return log_file


def _run_stage_with_autodebug(
    stage_name: str,
    fn: Callable[[], T],
    fn_for_source: Callable[..., Any],
    cfg: AppConfig,
    debug_agent: DebugAgent | None,
) -> T:
    """Esegue `fn`; in caso di eccezione, tenta un retry e chiede una diagnosi
    al DebugAgent scrivendo un report azionabile in logs/autodebug/.
    """
    max_retries = cfg.orchestrator.max_autodebug_retries
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - vogliamo intercettare tutto per il report
            last_exc = exc
            tb = traceback.format_exc()
            logger.error("Fase '%s' fallita (tentativo %d/%d): %s", stage_name, attempt, max_retries, exc)

            if debug_agent is not None and cfg.orchestrator.use_autodebug:
                _write_autodebug_report(stage_name, fn_for_source, tb, cfg, debug_agent)

            if attempt < max_retries:
                backoff = 2 ** attempt
                logger.info("Riprovo '%s' tra %ds...", stage_name, backoff)
                time.sleep(backoff)

    assert last_exc is not None
    raise last_exc


def _write_autodebug_report(
    stage_name: str,
    fn_for_source: Callable[..., Any],
    traceback_text: str,
    cfg: AppConfig,
    debug_agent: DebugAgent,
) -> None:
    try:
        source = inspect.getsource(fn_for_source)
    except (OSError, TypeError):
        source = "<sorgente non disponibile>"

    try:
        suggestion = debug_agent.suggest_fix(source, traceback_text)
    except LMStudioUnavailable as exc:
        logger.warning("Autodebug non disponibile (LM Studio irraggiungibile): %s", exc)
        return

    report_dir = Path(cfg.orchestrator.log_dir) / "autodebug"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{stage_name}_{time.strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(
        "# Autodebug report: {}\n\n"
        "## Diagnosi\n{}\n\n"
        "## Confidenza\n{}\n\n"
        "## Traceback originale\n```\n{}\n```\n\n"
        "## Patch suggerita (DA REVISIONARE PRIMA DI APPLICARE)\n```python\n{}\n```\n".format(
            stage_name,
            suggestion.get("diagnosis", "n/d"),
            suggestion.get("confidence", "n/d"),
            traceback_text,
            suggestion.get("fixed_code") or "# nessuna patch generata",
        ),
        encoding="utf-8",
    )
    logger.warning("Report di autodebug scritto in %s (revisiona prima di applicare la patch)", report_path)


class Pipeline:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.lm_client = LMStudioClient(cfg.lmstudio)
        self.vision_agent = VisionAgent(self.lm_client)
        self.debug_agent = DebugAgent(self.lm_client) if cfg.orchestrator.use_autodebug else None

    def run(
        self,
        image_path: str,
        n_parts: int = 1,
        axis: str | None = None,
        work_dir: str | None = None,
    ) -> PipelineResult:
        start = time.time()
        work_dir_p = Path(work_dir) if work_dir else Path(self.cfg.export.output_dir) / "_work"
        work_dir_p.mkdir(parents=True, exist_ok=True)

        vision_analysis = self._maybe_analyze_image(image_path)
        if axis in (None, "auto"):
            axis = (vision_analysis or {}).get("suggested_split_axis", "z")

        raw_mesh = _run_stage_with_autodebug(
            "generation",
            lambda: generation.generate_3d(image_path, self.cfg, work_dir_p),
            generation.generate_3d,
            self.cfg,
            self.debug_agent,
        )

        clean_mesh = _run_stage_with_autodebug(
            "mesh_cleanup",
            lambda: mesh_tools.clean_and_repair(raw_mesh, self.cfg.cleanup),
            mesh_tools.clean_and_repair,
            self.cfg,
            self.debug_agent,
        )
        clean_mesh = mesh_tools.normalize_scale(clean_mesh, self.cfg.generation.target_size_mm)

        if n_parts <= 1:
            auto_axis = cutter.auto_split_axis(clean_mesh, self.cfg.cutter.max_part_bbox_mm)
            if auto_axis is not None:
                logger.warning(
                    "Il pezzo eccede il piano di stampa lungo l'asse %s: forzo n_parts=2", auto_axis
                )
                n_parts = 2
                axis = auto_axis

        if n_parts > 1:
            parts = _run_stage_with_autodebug(
                "cutting",
                lambda: cutter.split_recursive(clean_mesh, self.cfg.cutter, self.cfg.cleanup, n_parts, axis or "z"),
                cutter.split_recursive,
                self.cfg,
                self.debug_agent,
            )
        else:
            parts = [cutter.CutPart(mesh=clean_mesh, label="part_1")]

        warnings: dict[str, list[str]] = {}
        parts_dict: dict[str, trimesh.Trimesh] = {}
        for part in parts:
            issues = mesh_tools.validate_printability(part.mesh, self.cfg.cutter.max_part_bbox_mm)
            if issues:
                warnings[part.label] = issues
                for issue in issues:
                    logger.warning("[%s] %s", part.label, issue)
            parts_dict[part.label] = part.mesh

        exported = exporter.export_all_parts(parts_dict, self.cfg.export)

        elapsed = time.time() - start
        logger.info("Pipeline completata in %.1fs: %d parti esportate", elapsed, len(parts))

        return PipelineResult(
            image_path=image_path,
            vision_analysis=vision_analysis,
            n_parts=len(parts),
            split_axis=axis,
            printability_warnings=warnings,
            exported_files=exported,
            elapsed_s=elapsed,
        )

    def _maybe_analyze_image(self, image_path: str) -> dict[str, Any] | None:
        if not self.cfg.orchestrator.use_vision_agent:
            return None
        try:
            analysis = self.vision_agent.analyze(image_path)
            logger.info("VisionAgent: %s", json.dumps(analysis, ensure_ascii=False))
            return analysis
        except LMStudioUnavailable as exc:
            logger.warning("VisionAgent saltato (LM Studio non raggiungibile): %s", exc)
            return None

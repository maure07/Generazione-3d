"""I passi del workflow, uno per classe.

Ogni passo implementa ``PipelineStep`` ed è indipendente dagli altri: riceve il
contesto, lo modifica e restituisce un messaggio in italiano per l'interfaccia.
L'ordine di esecuzione è definito in ``orchestrator.DEFAULT_STEPS``.

Sequenza completa:

1. Caricamento immagini      9. Eliminazione mesh flottanti
2. Analisi del prompt       10. Ottimizzazione triangoli
3. Generazione mesh AI      11. Riduzione poligoni
4. Riparazione automatica   12. Controllo errori STL
5. Solidificazione          13. Analisi della stampabilità
6. Chiusura dei buchi       14. Segmentazione intelligente
7. Correzione normali       15. Creazione incastri
8. Rimozione duplicati      16. Ottimizzazione AMS/MMU
                            17. Esportazione
"""

from __future__ import annotations

import abc
import logging
from pathlib import Path
from typing import Any

import trimesh

from ..ai.base import GenerationRequest
from ..ai.registry import registry
from ..ai.semantic import analyze_prompt
from ..domain.enums import IssueCode, Severity, StepId
from ..domain.models import BoundsInfo, Issue, PartInfo
from ..mesh.components import remove_floating_shells
from ..mesh.dedup import remove_duplicate_faces
from ..mesh.holes import close_holes
from ..mesh.io import is_empty, load_scene, scene_to_mesh
from ..mesh.metrics import compute_stats
from ..mesh.normals import fix_normals
from ..mesh.optimize import decimate, optimize_triangles
from ..mesh.repair import auto_repair, normalize_scale
from ..mesh.solidify import solidify
from ..mesh.validate import validate_stl
from .context import PipelineContext

logger = logging.getLogger(__name__)


class PipelineStep(abc.ABC):
    """Base di un passo del workflow."""

    #: Identificatore del passo.
    step_id: StepId
    #: Se ``True`` un errore in questo passo interrompe l'intera pipeline.
    critical: bool = True

    @property
    def label_it(self) -> str:
        return self.step_id.label_it

    def should_run(self, context: PipelineContext) -> bool:
        """Il passo va eseguito con le impostazioni correnti?"""
        return True

    @abc.abstractmethod
    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        """Esegue il passo.

        Returns:
            ``(messaggio in italiano, dettagli per il rapporto)``.
        """


# ---------------------------------------------------------------------------
# 1-2. Ingresso
# ---------------------------------------------------------------------------


class LoadImagesStep(PipelineStep):
    """Verifica che le immagini caricate esistano e siano leggibili."""

    step_id = StepId.LOAD_IMAGES

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        images = context.project.images
        if not images:
            raise ValueError(
                "Nessuna immagine caricata: aggiungere almeno un'immagine del soggetto"
            )

        valid: list[str] = []
        missing: list[str] = []
        for image in images:
            path = Path(image.path)
            if path.exists() and path.stat().st_size > 0:
                valid.append(image.filename)
            else:
                missing.append(image.filename)

        if not valid:
            raise ValueError("Nessuna delle immagini caricate è leggibile")

        message = f"{len(valid)} immagini pronte"
        if missing:
            message += f" ({len(missing)} non trovate e ignorate)"
        return message, {"valide": valid, "mancanti": missing}


class ReadPromptStep(PipelineStep):
    """Analizza la descrizione testuale per capire quali parti attendersi."""

    step_id = StepId.READ_PROMPT

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        analysis = analyze_prompt(context.project.prompt, context.project.negative_prompt)
        context.prompt_analysis = analysis
        return analysis.summary_it(), {
            "parti_attese": [p.value for p in analysis.ordered_parts],
            "parti_escluse": [p.value for p in analysis.excluded_parts],
            "umanoide": analysis.is_humanoid,
            "stile": analysis.style_hints,
        }


# ---------------------------------------------------------------------------
# 3. Generazione AI
# ---------------------------------------------------------------------------


class AIGenerationStep(PipelineStep):
    """Genera la mesh grezza a partire da immagini e prompt."""

    step_id = StepId.AI_MESH

    async def run_async(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        """Versione asincrona: i provider cloud fanno polling in rete."""
        settings = context.settings
        images = [Path(i.path) for i in context.project.images if Path(i.path).exists()]

        request = GenerationRequest(
            image_paths=images,
            prompt=context.project.prompt,
            negative_prompt=context.project.negative_prompt,
            target_faces=max(settings.optimization.target_faces * 2, 50_000),
            want_texture=True,
            want_parts=settings.segmentation.enabled,
            seed=settings.seed,
        )

        result = await registry.generate(
            settings.ai_provider, request, progress=context.progress
        )

        scene = load_scene(result.mesh_path)
        mesh = scene_to_mesh(scene)
        if is_empty(mesh):
            raise ValueError("Il generatore ha restituito un modello vuoto")

        # Se la scena contiene già più oggetti, li conserviamo per la segmentazione.
        if len(scene.geometry) > 1:
            context.provider_parts = {
                name: geom
                for name, geom in scene.geometry.items()
                if isinstance(geom, trimesh.Trimesh) and not is_empty(geom)
            }

        mesh, factor = normalize_scale(mesh, settings.target_height_mm)
        context.mesh = mesh
        context.scale_factor = factor
        context.report.stats_before = compute_stats(mesh)

        return (
            f"Modello generato con {result.provider} "
            f"({len(mesh.faces)} triangoli, {result.duration_s:.0f} s)"
        ), {
            "provider": result.provider,
            "facce": len(mesh.faces),
            "durata_s": round(result.duration_s, 1),
            "fattore_scala": round(factor, 4),
            "pezzi_dal_provider": len(context.provider_parts),
        }

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:  # pragma: no cover
        raise RuntimeError("Questo passo richiede l'esecuzione asincrona")


# ---------------------------------------------------------------------------
# 4-9. Pulizia e riparazione
# ---------------------------------------------------------------------------


class _MeshStep(PipelineStep):
    """Base dei passi che operano sulla mesh unica."""

    def mesh_of(self, context: PipelineContext) -> trimesh.Trimesh:
        if context.mesh is None or is_empty(context.mesh):
            raise ValueError("Nessuna mesh disponibile: la generazione non è riuscita")
        return context.mesh


class AutoRepairStep(_MeshStep):
    """Ripara la mesh grezza: duplicati, buchi, normali, gusci vaganti."""

    step_id = StepId.AUTO_REPAIR

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        repaired, report = auto_repair(
            mesh,
            remove_floaters_ratio=context.settings.optimization.remove_floaters_ratio,
        )
        context.mesh = repaired
        return report.message_it(), {
            "azioni": report.actions_it,
            "annullate": report.reverted_steps,
            "stagna": report.watertight_after,
            "valida": report.valid,
        }


class SolidifyStep(_MeshStep):
    """Trasforma superfici aperte in solidi e ispessisce le pareti sottili."""

    step_id = StepId.SOLIDIFY

    def should_run(self, context: PipelineContext) -> bool:
        return context.settings.solidify.enabled

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        config = context.settings.solidify
        solid, report = solidify(
            mesh,
            thickness_mm=config.shell_thickness_mm,
            min_wall_mm=context.settings.printer.min_printable_wall,
            make_hollow=config.hollow,
            drain_holes=config.drain_holes,
            drain_diameter_mm=config.drain_hole_diameter_mm,
        )
        context.mesh = solid
        return report.message_it(), {
            "era_aperta": report.was_open,
            "estrusa": report.extruded,
            "svuotata": report.hollowed,
            "vertici_ispessiti": report.thickened_vertices,
            "stagna": report.watertight,
        }


class CloseHolesStep(_MeshStep):
    """Chiude i buchi residui rendendo la mesh stagna."""

    step_id = StepId.CLOSE_HOLES

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        closed, report = close_holes(mesh)
        context.mesh = closed

        issues: list[Issue] = []
        if not report.watertight:
            issues.append(
                Issue(
                    code=IssueCode.OPEN_SURFACE,
                    severity=Severity.WARNING,
                    message_it=f"{report.holes_remaining} bordi aperti non richiudibili",
                    count=report.holes_remaining,
                )
            )
        return report.message_it(), {
            "buchi_iniziali": report.holes_before,
            "chiusi": report.holes_closed,
            "residui": report.holes_remaining,
            "metodi": report.method_used,
            "issues": [i.model_dump(mode="json") for i in issues],
        }


class FixNormalsStep(_MeshStep):
    """Rende coerenti le normali e le orienta verso l'esterno."""

    step_id = StepId.FIX_NORMALS

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        fixed, report = fix_normals(mesh, force_outward=True)
        context.mesh = fixed
        return report.message_it(), {
            "winding_corretto": report.winding_fixed,
            "inversione_corretta": report.inversion_fixed,
            "facce_girate": report.flipped_faces,
            "coerenti": report.consistent,
        }


class RemoveDuplicatesStep(_MeshStep):
    """Elimina facce duplicate, degeneri e vertici coincidenti."""

    step_id = StepId.REMOVE_DUPLICATES

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        cleaned, report = remove_duplicate_faces(
            mesh, merge_distance=context.settings.optimization.weld_distance_mm
        )
        context.mesh = cleaned
        return report.message_it(), {
            "facce_duplicate": report.duplicate_faces,
            "facce_degeneri": report.degenerate_faces,
            "vertici_fusi": report.merged_vertices,
        }


class RemoveFloatersStep(_MeshStep):
    """Rimuove i gusci staccati troppo piccoli per essere significativi."""

    step_id = StepId.REMOVE_FLOATERS

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        cleaned, report = remove_floating_shells(
            mesh, min_volume_ratio=context.settings.optimization.remove_floaters_ratio
        )
        context.mesh = cleaned
        return report.message_it(), {
            "componenti_prima": report.components_before,
            "componenti_dopo": report.components_after,
            "rimossi": report.removed,
        }


# ---------------------------------------------------------------------------
# 10-11. Ottimizzazione
# ---------------------------------------------------------------------------


class OptimizeTrianglesStep(_MeshStep):
    """Regolarizza la maglia eliminando schegge e spigoli microscopici."""

    step_id = StepId.OPTIMIZE_TRIANGLES

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        optimized, report = optimize_triangles(
            mesh, min_edge_mm=context.settings.optimization.weld_distance_mm
        )
        context.mesh = optimized
        return report.message_it(), {
            "schegge_rimosse": report.slivers_removed,
            "spigoli_collassati": report.short_edges_collapsed,
            "facce": report.faces_after,
        }


class DecimateStep(_MeshStep):
    """Riduce il numero di triangoli conservando il dettaglio."""

    step_id = StepId.DECIMATE

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        config = context.settings.optimization
        reduced, report = decimate(
            mesh,
            target_faces=config.target_faces,
            preserve_detail=config.preserve_detail,
            adaptive=config.adaptive,
        )
        context.mesh = reduced
        return report.message_it(), {
            "facce_prima": report.faces_before,
            "facce_dopo": report.faces_after,
            "riduzione_pct": round(report.reduction_pct, 1),
            "motore": report.engine,
            "scarto_mm": round(report.hausdorff_estimate_mm, 4),
        }


# ---------------------------------------------------------------------------
# 12-13. Verifica
# ---------------------------------------------------------------------------


class ValidateStep(_MeshStep):
    """Controllo formale degli errori STL."""

    step_id = StepId.VALIDATE_STL

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        mesh = self.mesh_of(context)
        report = validate_stl(mesh, check_self_intersections=True)
        context.report.issues.extend(report.issues)
        return report.message_it(), {
            "valido": report.valid,
            "stagno": report.watertight,
            "spigoli_aperti": report.open_edges,
            "non_manifold": report.non_manifold_edges,
            "volume_mm3": round(report.volume_mm3, 1),
        }


class PrintabilityStep(_MeshStep):
    """Analizza la stampabilità e applica le correzioni automatiche."""

    step_id = StepId.PRINTABILITY

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        from ..printability import AutoFixer, PrintabilityAnalyzer

        mesh = self.mesh_of(context)
        printer = context.settings.printer
        analyzer = PrintabilityAnalyzer(printer)
        analysis = analyzer.analyze(mesh, part_name="modello completo")

        details: dict[str, Any] = {
            "punteggio_iniziale": analysis.score,
            "problemi": [i.message_it for i in analysis.issues],
            "tempo_stimato_min": round(analysis.estimated_time_min, 1),
            "filamento_stimato_g": round(analysis.estimated_filament_g, 1),
        }

        if context.settings.auto_fix and analysis.issues:
            context.progress(0.5, "Correzione automatica dei difetti rilevati")
            fixed, fix_report = AutoFixer(printer).fix(mesh, analysis)
            context.mesh = fixed
            analysis = analyzer.analyze(fixed, part_name="modello completo")
            details.update(
                {
                    "correzioni_applicate": fix_report.applied_it,
                    "correzioni_annullate": fix_report.reverted_it,
                    "non_correggibili": fix_report.not_fixable_it,
                }
            )

        details["punteggio_finale"] = analysis.score
        context.report.printability_score = analysis.score
        context.report.issues.extend(analysis.issues)
        return analysis.message_it(), details


# ---------------------------------------------------------------------------
# 14-15. Segmentazione e incastri
# ---------------------------------------------------------------------------


class SegmentationStep(_MeshStep):
    """Divide il modello nei pezzi stampabili separatamente."""

    step_id = StepId.SEGMENTATION

    def should_run(self, context: PipelineContext) -> bool:
        return context.settings.segmentation.enabled

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        from ..segmentation.segmenter import Segmenter

        mesh = self.mesh_of(context)
        analysis = context.prompt_analysis or analyze_prompt(context.project.prompt)

        segmenter = Segmenter(context.settings.segmentation)
        result = segmenter.segment(mesh, analysis, pre_split=context.provider_parts or None)
        context.parts = result.parts

        return result.message_it(), {
            "metodo": result.method_it,
            "pezzi": [
                {"nome": p.name, "tipo": p.part_type.value, "confidenza": round(p.confidence, 2)}
                for p in result.parts
            ],
            "note": result.notes_it,
        }


class JoineryStep(PipelineStep):
    """Crea spine e alloggiamenti fra i pezzi adiacenti."""

    step_id = StepId.JOINERY

    def should_run(self, context: PipelineContext) -> bool:
        return context.settings.joinery.enabled and len(context.parts) > 1

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        from ..joinery import JoineryPlanner

        planner = JoineryPlanner(context.settings.joinery, context.settings.printer)
        result = planner.apply(context.parts)
        context.connectors = result.connectors
        context.report.connectors = result.connectors

        return result.message_it(), {
            "incastri": [c.description_it for c in result.connectors],
            "saltati": result.skipped_it,
            "note": result.notes_it,
        }


# ---------------------------------------------------------------------------
# 16-17. Colori ed esportazione
# ---------------------------------------------------------------------------


class AMSStep(PipelineStep):
    """Costruisce il piano multicolore per il sistema AMS/MMU."""

    step_id = StepId.AMS_OPTIMIZE

    def should_run(self, context: PipelineContext) -> bool:
        return context.settings.ams.enabled

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        from ..ams import AMSOptimizer

        parts = context.ensure_parts()
        if not parts:
            return "Nessun pezzo da colorare", {}

        optimizer = AMSOptimizer(context.settings.ams, context.settings.printer)
        result = optimizer.optimize(context.parts_map(), context.colors_map())
        context.ams_plan = result.plan
        context.report.ams_plan = result.plan

        return result.message_it(), {
            "colori": result.plan.slots,
            "cambi_filamento": result.plan.color_changes,
            "cambi_senza_ottimizzazione": result.plan.color_changes_naive,
            "spurgo_risparmiato_mm3": round(result.plan.purge_waste_saved_mm3, 1),
            "note": result.plan.notes_it,
        }


class ExportStep(PipelineStep):
    """Scrive i file nei formati richiesti e le istruzioni di montaggio."""

    step_id = StepId.EXPORT

    def run(self, context: PipelineContext) -> tuple[str, dict[str, Any]]:
        from ..exporters import ExportItem, build_assembly_instructions, export_all
        from ..exporters.slicers import recommended_formats, slicer_notes_it

        parts = context.ensure_parts()
        if not parts:
            raise ValueError("Nessun pezzo da esportare")

        plan = context.ams_plan
        items = [
            ExportItem(
                part_id=part.id,
                name=part.name,
                mesh=part.mesh,
                color_hex=part.color_hex,
                ams_slot=plan.part_assignment.get(part.id) if plan else None,
            )
            for part in parts
            if not is_empty(part.mesh)
        ]

        formats = list(context.settings.export_formats)
        for fmt in recommended_formats(context.settings.slicer_targets):
            if fmt not in formats:
                formats.append(fmt)

        destination = context.output_dir("export")
        files = export_all(items, formats, destination, combined=True)
        context.report.exports = files

        # Riepilogo dei pezzi nel rapporto finale.
        context.report.parts = [_part_info(part, plan) for part in parts]

        instructions = build_assembly_instructions(
            context.report.parts, context.connectors, plan
        )
        instructions_path = destination / "istruzioni_montaggio.md"
        instructions_path.write_text(instructions, encoding="utf-8")

        notes = slicer_notes_it(context.settings.slicer_targets)
        return (
            f"Esportati {len(files)} file in {len(formats)} formati "
            f"({', '.join(f.value.upper() for f in formats)})"
        ), {
            "cartella": str(destination),
            "file": len(files),
            "formati": [f.value for f in formats],
            "istruzioni": str(instructions_path),
            "note_slicer": notes,
        }


def _part_info(part, plan) -> PartInfo:
    """Converte un pezzo interno nel modello di dominio esposto dall'API."""
    mesh = part.mesh
    bounds = None
    if not is_empty(mesh):
        raw = mesh.bounds
        bounds = BoundsInfo(
            min=tuple(float(v) for v in raw[0]), max=tuple(float(v) for v in raw[1])
        )

    return PartInfo(
        id=part.id,
        name=part.name,
        part_type=part.part_type,
        side=part.side,
        faces=int(len(mesh.faces)) if not is_empty(mesh) else 0,
        vertices=int(len(mesh.vertices)) if not is_empty(mesh) else 0,
        volume_mm3=round(part.volume_mm3, 2),
        area_mm2=round(float(mesh.area), 2) if not is_empty(mesh) else 0.0,
        bounds=bounds,
        color_hex=part.color_hex,
        ams_slot=plan.part_assignment.get(part.id) if plan else None,
        watertight=bool(mesh.is_watertight) if not is_empty(mesh) else False,
        confidence=round(part.confidence, 3),
    )

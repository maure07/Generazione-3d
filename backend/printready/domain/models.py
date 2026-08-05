"""Modelli di dominio (Pydantic v2) usati da API, pipeline e persistenza.

Questi modelli rappresentano il *contratto* fra backend e frontend: ogni campo
è serializzabile in JSON e ogni valore di default è pensato per essere sicuro
per una stampante FDM 0.4 mm.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    ExportFormat,
    IssueCode,
    JobState,
    JoineryType,
    PartType,
    Severity,
    SlicerTarget,
    StepId,
    UIMode,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class Base(BaseModel):
    """Base comune: consente popolamento da attributi e vieta campi ignoti."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Impostazioni di stampa e di pipeline
# ---------------------------------------------------------------------------


class PrinterProfile(Base):
    """Caratteristiche fisiche della stampante di destinazione."""

    name: str = "FDM generica 0.4 mm"
    nozzle_diameter_mm: float = Field(0.4, gt=0.05, le=2.0)
    layer_height_mm: float = Field(0.2, gt=0.02, le=1.0)
    bed_size_mm: tuple[float, float, float] = (256.0, 256.0, 256.0)
    max_overhang_deg: float = Field(
        45.0, ge=10.0, le=89.0, description="Angolo massimo stampabile senza supporti"
    )
    min_wall_mm: float = Field(
        0.8, gt=0.05, le=10.0, description="Spessore minimo di parete accettabile"
    )
    min_feature_mm: float = Field(
        0.6, gt=0.05, le=10.0, description="Dimensione minima di un dettaglio riproducibile"
    )
    has_ams: bool = Field(True, description="Sistema multimateriale AMS/MMU disponibile")
    ams_slots: int = Field(4, ge=1, le=16)
    purge_volume_mm3: float = Field(
        140.0, ge=0.0, description="Volume di spurgo per cambio colore (torre di purge)"
    )

    @property
    def min_printable_wall(self) -> float:
        """Parete minima effettiva: il massimo fra profilo e due estrusioni."""
        return max(self.min_wall_mm, self.nozzle_diameter_mm * 2.0)


class JoinerySettings(Base):
    """Parametri del sistema di incastri."""

    enabled: bool = True
    joint_type: JoineryType = JoineryType.CYLINDRICAL_PIN
    tolerance_mm: float = Field(
        0.15,
        ge=0.05,
        le=0.5,
        description="Gioco fra spina e alloggiamento (0,05 - 0,5 mm)",
    )
    pin_diameter_mm: float = Field(4.0, gt=0.5, le=30.0)
    pin_length_mm: float = Field(6.0, gt=0.5, le=60.0)
    conical_taper_ratio: float = Field(
        0.75, gt=0.2, le=1.0, description="Rapporto diametro punta/base per spine coniche"
    )
    magnet_diameter_mm: float = Field(6.0, gt=1.0, le=30.0)
    magnet_height_mm: float = Field(3.0, gt=0.5, le=20.0)
    magnet_recess_mm: float = Field(0.2, ge=0.0, le=2.0)
    auto_scale_to_part: bool = Field(
        True, description="Adatta le dimensioni della spina alla superficie di contatto"
    )
    anti_rotation: bool = Field(
        True, description="Aggiunge una seconda spina di antirotazione quando c'è spazio"
    )

    @field_validator("tolerance_mm")
    @classmethod
    def _check_tolerance(cls, v: float) -> float:
        # Ribadito esplicitamente: il range richiesto dal capitolato è 0,05-0,5 mm.
        if not (0.05 <= v <= 0.5):
            raise ValueError("La tolleranza deve essere compresa fra 0,05 e 0,5 mm")
        return round(v, 3)


class SegmentationSettings(Base):
    """Parametri della segmentazione intelligente in stile Funko Pop."""

    enabled: bool = True
    max_parts: int = Field(24, ge=1, le=200)
    min_part_volume_ratio: float = Field(
        0.0005, gt=0.0, le=0.5, description="Volume minimo di un pezzo rispetto al totale"
    )
    merge_tiny_parts: bool = True
    separate_by_color: bool = Field(
        True, description="Usa i colori del modello per distinguere i componenti"
    )
    use_anatomical_priors: bool = Field(
        True, description="Applica proporzioni antropometriche per etichettare i pezzi"
    )
    forced_labels: list[PartType] = Field(
        default_factory=list, description="Etichette che devono comparire nel risultato"
    )
    split_symmetric_pairs: bool = Field(
        True, description="Divide braccia/gambe/mani in destra e sinistra"
    )


class OptimizationSettings(Base):
    """Parametri di ottimizzazione topologica e riduzione poligoni."""

    target_faces: int = Field(
        120_000, ge=500, le=5_000_000, description="Budget triangoli del modello finale"
    )
    preserve_detail: float = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="0 = massima riduzione, 1 = massima conservazione del dettaglio",
    )
    adaptive: bool = Field(
        True, description="Riduce meno nelle zone ad alta curvatura (volti, dettagli)"
    )
    weld_distance_mm: float = Field(0.01, ge=0.0, le=1.0)
    remove_floaters_ratio: float = Field(
        0.02, ge=0.0, le=1.0, description="Gusci sotto questa frazione di volume vengono rimossi"
    )


class SolidifySettings(Base):
    """Parametri di solidificazione (da superficie a solido stampabile)."""

    enabled: bool = True
    shell_thickness_mm: float = Field(1.6, gt=0.1, le=20.0)
    hollow: bool = Field(False, description="Svuota il modello mantenendo un guscio")
    drain_holes: int = Field(2, ge=0, le=8, description="Fori di drenaggio quando è cavo")
    drain_hole_diameter_mm: float = Field(4.0, gt=0.5, le=20.0)


class AMSSettings(Base):
    """Parametri di ottimizzazione multicolore AMS/MMU."""

    enabled: bool = True
    max_colors: int = Field(4, ge=1, le=16)
    minimize_swaps: bool = True
    prefer_part_split_over_swap: bool = Field(
        True,
        description="Preferisce separare fisicamente i colori invece di cambiare filamento",
    )
    max_acceptable_swaps: int = Field(12, ge=0, le=500)


class GenerationSettings(Base):
    """Impostazioni complete di una generazione."""

    printer: PrinterProfile = Field(default_factory=PrinterProfile)
    joinery: JoinerySettings = Field(default_factory=JoinerySettings)
    segmentation: SegmentationSettings = Field(default_factory=SegmentationSettings)
    optimization: OptimizationSettings = Field(default_factory=OptimizationSettings)
    solidify: SolidifySettings = Field(default_factory=SolidifySettings)
    ams: AMSSettings = Field(default_factory=AMSSettings)

    target_height_mm: float = Field(
        120.0, gt=5.0, le=1000.0, description="Altezza finale desiderata del modello assemblato"
    )
    ai_provider: str = Field("auto", description="tripo | meshy | hunyuan3d | local | auto")
    export_formats: list[ExportFormat] = Field(
        default_factory=lambda: [ExportFormat.THREEMF, ExportFormat.STL]
    )
    slicer_targets: list[SlicerTarget] = Field(
        default_factory=lambda: [SlicerTarget.BAMBU_STUDIO, SlicerTarget.ORCA_SLICER]
    )
    ui_mode: UIMode = UIMode.BEGINNER
    auto_fix: bool = Field(True, description="Correggi automaticamente i difetti rilevati")
    seed: int | None = None


# ---------------------------------------------------------------------------
# Ingresso / uscita della pipeline
# ---------------------------------------------------------------------------


class ImageRef(Base):
    """Riferimento a un'immagine caricata dall'utente."""

    id: str = Field(default_factory=_new_id)
    filename: str
    path: str
    width: int = 0
    height: int = 0
    view: Literal["front", "back", "left", "right", "top", "auto"] = "auto"
    is_primary: bool = False


class BoundsInfo(Base):
    """Ingombro di un pezzo, in millimetri."""

    min: tuple[float, float, float]
    max: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return (
            self.max[0] - self.min[0],
            self.max[1] - self.min[1],
            self.max[2] - self.min[2],
        )


class PartInfo(Base):
    """Metadati di un singolo pezzo stampabile risultante dalla segmentazione."""

    id: str = Field(default_factory=_new_id)
    name: str
    part_type: PartType = PartType.UNKNOWN
    side: Literal["center", "left", "right"] = "center"
    faces: int = 0
    vertices: int = 0
    volume_mm3: float = 0.0
    area_mm2: float = 0.0
    bounds: BoundsInfo | None = None
    color_hex: str | None = None
    ams_slot: int | None = None
    watertight: bool = False
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    parent_id: str | None = None
    file_path: str | None = None
    connectors: list[str] = Field(default_factory=list)

    @property
    def label_it(self) -> str:
        return self.part_type.label_it


class ConnectorInfo(Base):
    """Un incastro generato fra due pezzi."""

    id: str = Field(default_factory=_new_id)
    joint_type: JoineryType
    male_part_id: str
    female_part_id: str
    position: tuple[float, float, float]
    direction: tuple[float, float, float]
    diameter_mm: float
    length_mm: float
    tolerance_mm: float
    magnet_spec: str | None = None

    @property
    def description_it(self) -> str:
        return (
            f"{self.joint_type.label_it} Ø{self.diameter_mm:.1f} mm "
            f"× {self.length_mm:.1f} mm (gioco {self.tolerance_mm:.2f} mm)"
        )


class Issue(Base):
    """Problema rilevato dall'analisi di stampabilità."""

    code: IssueCode
    severity: Severity
    message_it: str
    part_id: str | None = None
    count: int = 1
    locations: list[tuple[float, float, float]] = Field(default_factory=list)
    auto_fixed: bool = False
    fix_description_it: str | None = None

    @property
    def label_it(self) -> str:
        return self.code.label_it


class MeshStats(Base):
    """Statistiche geometriche di una mesh o di una scena."""

    vertices: int = 0
    faces: int = 0
    components: int = 1
    volume_mm3: float = 0.0
    area_mm2: float = 0.0
    watertight: bool = False
    winding_consistent: bool = False
    euler_number: int = 0
    bounds: BoundsInfo | None = None


class AMSPlan(Base):
    """Piano multicolore risultante dall'ottimizzazione AMS/MMU."""

    slots: dict[int, str] = Field(default_factory=dict, description="slot -> colore HEX")
    slot_names_it: dict[int, str] = Field(default_factory=dict)
    part_assignment: dict[str, int] = Field(default_factory=dict, description="part_id -> slot")
    print_order: list[str] = Field(default_factory=list, description="Ordine di stampa dei pezzi")
    color_changes: int = 0
    color_changes_naive: int = 0
    purge_waste_mm3: float = 0.0
    purge_waste_saved_mm3: float = 0.0
    estimated_time_saved_min: float = 0.0
    notes_it: list[str] = Field(default_factory=list)


class ExportedFile(Base):
    """File prodotto dall'esportazione."""

    format: ExportFormat
    path: str
    filename: str
    size_bytes: int = 0
    part_id: str | None = None
    contains_all_parts: bool = False
    slicer_hint_it: str | None = None


class StepResult(Base):
    """Esito di un singolo passo della pipeline."""

    step: StepId
    state: JobState = JobState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float = 0.0
    message_it: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)

    @property
    def label_it(self) -> str:
        return self.step.label_it


class PipelineReport(Base):
    """Rapporto completo di una esecuzione della pipeline."""

    job_id: str
    project_id: str
    state: JobState = JobState.PENDING
    created_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    steps: list[StepResult] = Field(default_factory=list)
    parts: list[PartInfo] = Field(default_factory=list)
    connectors: list[ConnectorInfo] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    stats_before: MeshStats | None = None
    stats_after: MeshStats | None = None
    ams_plan: AMSPlan | None = None
    exports: list[ExportedFile] = Field(default_factory=list)
    printability_score: float = Field(0.0, ge=0.0, le=100.0)
    summary_it: str = ""
    error_it: str | None = None

    @property
    def duration_s(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.created_at).total_seconds()


# ---------------------------------------------------------------------------
# Progetti, cronologia, undo/redo
# ---------------------------------------------------------------------------


class ProjectSummary(Base):
    """Vista sintetica di un progetto per la cronologia."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    thumbnail_path: str | None = None
    prompt: str = ""
    parts_count: int = 0
    last_state: JobState = JobState.PENDING


class Project(Base):
    """Progetto completo con immagini, prompt, impostazioni e ultimo report."""

    id: str = Field(default_factory=_new_id)
    name: str = "Nuovo progetto"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    prompt: str = ""
    negative_prompt: str = ""
    images: list[ImageRef] = Field(default_factory=list)
    settings: GenerationSettings = Field(default_factory=GenerationSettings)
    last_report: PipelineReport | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    thumbnail_path: str | None = None

    def summary(self) -> ProjectSummary:
        return ProjectSummary(
            id=self.id,
            name=self.name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            thumbnail_path=self.thumbnail_path,
            prompt=self.prompt,
            parts_count=len(self.last_report.parts) if self.last_report else 0,
            last_state=self.last_report.state if self.last_report else JobState.PENDING,
        )


class HistoryEntry(Base):
    """Voce dello stack undo/redo (illimitato, persistito su disco)."""

    id: str = Field(default_factory=_new_id)
    project_id: str
    label_it: str
    created_at: datetime = Field(default_factory=_now)
    snapshot: dict[str, Any]


# ---------------------------------------------------------------------------
# Richieste API
# ---------------------------------------------------------------------------


class CreateProjectRequest(Base):
    name: str = "Nuovo progetto"
    prompt: str = ""
    negative_prompt: str = ""
    settings: GenerationSettings | None = None


class UpdateProjectRequest(Base):
    name: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    settings: GenerationSettings | None = None
    notes: str | None = None
    tags: list[str] | None = None


class GenerateRequest(Base):
    """Avvia la pipeline completa su un progetto."""

    project_id: str
    settings: GenerationSettings | None = None
    steps_from: StepId | None = Field(
        None, description="Riprende la pipeline da un passo specifico (modalità esperto)"
    )
    dry_run: bool = False


class BatchGenerateRequest(Base):
    """Elaborazione batch di più progetti in parallelo."""

    project_ids: list[str] = Field(min_length=1)
    max_parallel: int = Field(2, ge=1, le=16)
    settings: GenerationSettings | None = None


class JobStatus(Base):
    """Stato corrente di un job, trasmesso anche via WebSocket."""

    job_id: str
    project_id: str
    state: JobState
    current_step: StepId | None = None
    step_index: int = 0
    step_count: int = 0
    progress: float = Field(0.0, ge=0.0, le=1.0)
    message_it: str = ""
    report: PipelineReport | None = None
    error_it: str | None = None

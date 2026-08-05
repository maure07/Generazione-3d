"""Enumerazioni di dominio condivise da tutti i moduli di PrintReady AI.

Tutte le etichette utente sono in italiano (`label_it`), mentre i valori tecnici
restano in inglese per garantire stabilità di serializzazione e compatibilità
con le API esterne.
"""

from __future__ import annotations

from enum import Enum


class PartType(str, Enum):
    """Categorie semantiche riconosciute automaticamente dal segmentatore.

    L'elenco copre i componenti tipici di una figura in stile *Funko Pop*
    e viene usato sia dal riconoscitore AI sia dal pianificatore di incastri
    (che decide dove e come inserire le spine).
    """

    HEAD = "head"
    HAIR = "hair"
    HAT = "hat"
    BEARD = "beard"
    MUSTACHE = "mustache"
    EYES = "eyes"
    EYEBROWS = "eyebrows"
    EARS = "ears"
    BODY = "body"
    ARMS = "arms"
    HANDS = "hands"
    LEGS = "legs"
    SHOES = "shoes"
    CLOTHES = "clothes"
    CAPE = "cape"
    ACCESSORY = "accessory"
    WEAPON = "weapon"
    BASE = "base"
    DECORATION = "decoration"
    UNKNOWN = "unknown"

    @property
    def label_it(self) -> str:
        """Nome mostrato nell'interfaccia utente (italiano)."""
        return _PART_LABELS_IT[self]

    @property
    def is_appendage(self) -> bool:
        """Indica se la parte è un'appendice sottile che richiede spine robuste."""
        return self in {
            PartType.ARMS,
            PartType.HANDS,
            PartType.LEGS,
            PartType.WEAPON,
            PartType.ACCESSORY,
            PartType.EARS,
        }


_PART_LABELS_IT: dict[PartType, str] = {
    PartType.HEAD: "Testa",
    PartType.HAIR: "Capelli",
    PartType.HAT: "Cappello",
    PartType.BEARD: "Barba",
    PartType.MUSTACHE: "Baffi",
    PartType.EYES: "Occhi",
    PartType.EYEBROWS: "Sopracciglia",
    PartType.EARS: "Orecchie",
    PartType.BODY: "Corpo",
    PartType.ARMS: "Braccia",
    PartType.HANDS: "Mani",
    PartType.LEGS: "Gambe",
    PartType.SHOES: "Scarpe",
    PartType.CLOTHES: "Vestiti",
    PartType.CAPE: "Mantello",
    PartType.ACCESSORY: "Accessori",
    PartType.WEAPON: "Armi",
    PartType.BASE: "Basetta",
    PartType.DECORATION: "Elementi decorativi",
    PartType.UNKNOWN: "Non classificato",
}


class JoineryType(str, Enum):
    """Tipologie di incastro generabili automaticamente."""

    CYLINDRICAL_PIN = "cylindrical_pin"
    CONICAL_PIN = "conical_pin"
    SQUARE_PIN = "square_pin"
    MAGNET = "magnet"
    NONE = "none"

    @property
    def label_it(self) -> str:
        return {
            JoineryType.CYLINDRICAL_PIN: "Spina cilindrica",
            JoineryType.CONICAL_PIN: "Spina conica",
            JoineryType.SQUARE_PIN: "Incastro quadrato",
            JoineryType.MAGNET: "Incastro magnetico",
            JoineryType.NONE: "Nessuno",
        }[self]


class Severity(str, Enum):
    """Gravità di un problema rilevato dall'analisi di stampabilità."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def label_it(self) -> str:
        return {
            Severity.INFO: "Informazione",
            Severity.WARNING: "Avviso",
            Severity.ERROR: "Errore",
            Severity.CRITICAL: "Critico",
        }[self]


class IssueCode(str, Enum):
    """Codici dei difetti riconosciuti dal controllo di stampa."""

    THIN_WALL = "thin_wall"
    ISLAND = "island"
    OPEN_SURFACE = "open_surface"
    INVERTED_NORMALS = "inverted_normals"
    NON_MANIFOLD = "non_manifold"
    SELF_INTERSECTION = "self_intersection"
    OVERHANG = "overhang"
    FLOATING_SHELL = "floating_shell"
    DEGENERATE_FACE = "degenerate_face"
    DUPLICATE_FACE = "duplicate_face"
    ZERO_VOLUME = "zero_volume"
    OVERSIZED = "oversized"
    UNPRINTABLE_FEATURE = "unprintable_feature"

    @property
    def label_it(self) -> str:
        return {
            IssueCode.THIN_WALL: "Parete troppo sottile",
            IssueCode.ISLAND: "Isola non supportata",
            IssueCode.OPEN_SURFACE: "Superficie aperta",
            IssueCode.INVERTED_NORMALS: "Normali invertite",
            IssueCode.NON_MANIFOLD: "Geometria non manifold",
            IssueCode.SELF_INTERSECTION: "Autointersezione",
            IssueCode.OVERHANG: "Sbalzo eccessivo",
            IssueCode.FLOATING_SHELL: "Mesh flottante",
            IssueCode.DEGENERATE_FACE: "Faccia degenere",
            IssueCode.DUPLICATE_FACE: "Faccia duplicata",
            IssueCode.ZERO_VOLUME: "Volume nullo",
            IssueCode.OVERSIZED: "Fuori dal volume di stampa",
            IssueCode.UNPRINTABLE_FEATURE: "Dettaglio non stampabile",
        }[self]


class ExportFormat(str, Enum):
    """Formati di esportazione supportati."""

    STL = "stl"
    OBJ = "obj"
    THREEMF = "3mf"
    STEP = "step"
    GLB = "glb"

    @property
    def extension(self) -> str:
        return {
            ExportFormat.STL: ".stl",
            ExportFormat.OBJ: ".obj",
            ExportFormat.THREEMF: ".3mf",
            ExportFormat.STEP: ".step",
            ExportFormat.GLB: ".glb",
        }[self]

    @property
    def supports_color(self) -> bool:
        """Indica se il formato conserva le informazioni di colore/materiale."""
        return self in {ExportFormat.THREEMF, ExportFormat.GLB, ExportFormat.OBJ}


class SlicerTarget(str, Enum):
    """Slicer per cui l'app genera pacchetti e profili compatibili."""

    BAMBU_STUDIO = "bambu_studio"
    ORCA_SLICER = "orca_slicer"
    CURA = "cura"
    PRUSA_SLICER = "prusa_slicer"
    ANYCUBIC_SLICER = "anycubic_slicer"

    @property
    def label_it(self) -> str:
        return {
            SlicerTarget.BAMBU_STUDIO: "Bambu Studio",
            SlicerTarget.ORCA_SLICER: "OrcaSlicer",
            SlicerTarget.CURA: "Cura",
            SlicerTarget.PRUSA_SLICER: "PrusaSlicer",
            SlicerTarget.ANYCUBIC_SLICER: "Anycubic Slicer",
        }[self]


class UIMode(str, Enum):
    """Modalità dell'interfaccia."""

    BEGINNER = "beginner"
    EXPERT = "expert"


class JobState(str, Enum):
    """Stato di un job della pipeline."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepId(str, Enum):
    """Identificatori dei 15 passi del workflow (ordine di esecuzione)."""

    LOAD_IMAGES = "load_images"
    READ_PROMPT = "read_prompt"
    AI_MESH = "ai_mesh"
    AUTO_REPAIR = "auto_repair"
    SOLIDIFY = "solidify"
    CLOSE_HOLES = "close_holes"
    FIX_NORMALS = "fix_normals"
    REMOVE_DUPLICATES = "remove_duplicates"
    REMOVE_FLOATERS = "remove_floaters"
    OPTIMIZE_TRIANGLES = "optimize_triangles"
    DECIMATE = "decimate"
    VALIDATE_STL = "validate_stl"
    PRINTABILITY = "printability"
    SEGMENTATION = "segmentation"
    JOINERY = "joinery"
    AMS_OPTIMIZE = "ams_optimize"
    EXPORT = "export"

    @property
    def label_it(self) -> str:
        return _STEP_LABELS_IT[self]


_STEP_LABELS_IT: dict[StepId, str] = {
    StepId.LOAD_IMAGES: "Caricamento immagini",
    StepId.READ_PROMPT: "Analisi del prompt",
    StepId.AI_MESH: "Generazione mesh AI",
    StepId.AUTO_REPAIR: "Riparazione automatica",
    StepId.SOLIDIFY: "Solidificazione della mesh",
    StepId.CLOSE_HOLES: "Chiusura automatica dei buchi",
    StepId.FIX_NORMALS: "Correzione delle normali",
    StepId.REMOVE_DUPLICATES: "Rimozione facce duplicate",
    StepId.REMOVE_FLOATERS: "Eliminazione mesh flottanti",
    StepId.OPTIMIZE_TRIANGLES: "Ottimizzazione triangoli",
    StepId.DECIMATE: "Riduzione poligoni",
    StepId.VALIDATE_STL: "Controllo errori STL",
    StepId.PRINTABILITY: "Analisi della stampabilità",
    StepId.SEGMENTATION: "Segmentazione intelligente",
    StepId.JOINERY: "Creazione incastri",
    StepId.AMS_OPTIMIZE: "Ottimizzazione AMS/MMU",
    StepId.EXPORT: "Esportazione",
}

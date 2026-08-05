"""Analisi anatomica del modello: dove tagliare una figura.

L'idea centrale è che una figura in piedi ha una "firma" riconoscibile nel
**profilo delle sezioni orizzontali**: percorrendo il modello dal basso verso
l'alto e misurando area e numero di isole di ogni sezione, si individuano
caviglie, biforcazione delle gambe, vita, spalle e collo senza alcun modello
addestrato.

Le proporzioni di riferimento (canone classico e canone Funko Pop) servono solo
come *prior*: se la geometria contraddice il prior, vince la geometria.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

from ..domain.enums import PartType

logger = logging.getLogger(__name__)


#: Proporzioni tipiche (frazione dell'altezza totale, dal basso).
#: Il canone Funko Pop ha testa enorme: circa il 40% dell'altezza.
FUNKO_PROPORTIONS: dict[str, tuple[float, float]] = {
    "base": (0.00, 0.04),
    "shoes": (0.02, 0.10),
    "legs": (0.08, 0.34),
    "body": (0.30, 0.60),
    "arms": (0.34, 0.60),
    "neck": (0.58, 0.63),
    "head": (0.60, 1.00),
}

#: Canone realistico a 7,5 teste.
REALISTIC_PROPORTIONS: dict[str, tuple[float, float]] = {
    "base": (0.00, 0.03),
    "shoes": (0.02, 0.08),
    "legs": (0.06, 0.48),
    "body": (0.45, 0.80),
    "arms": (0.50, 0.80),
    "neck": (0.80, 0.86),
    "head": (0.84, 1.00),
}


@dataclass(slots=True)
class SliceProfile:
    """Profilo di una sezione orizzontale del modello."""

    z: float
    height_ratio: float
    area_mm2: float
    islands: int
    width_x: float
    width_y: float
    centroids: list[tuple[float, float]] = field(default_factory=list)


@dataclass(slots=True)
class AnatomyAnalysis:
    """Punti notevoli individuati sulla figura, in coordinate assolute Z."""

    height_mm: float = 0.0
    z_min: float = 0.0
    z_max: float = 0.0
    profiles: list[SliceProfile] = field(default_factory=list)
    neck_z: float | None = None
    shoulder_z: float | None = None
    waist_z: float | None = None
    crotch_z: float | None = None
    ankle_z: float | None = None
    base_top_z: float | None = None
    arm_split_x: tuple[float, float] | None = None
    is_humanoid: bool = False
    is_funko: bool = False
    head_ratio: float = 0.0
    notes_it: list[str] = field(default_factory=list)

    def ratio_of(self, z: float) -> float:
        """Converte una quota assoluta in frazione dell'altezza."""
        if self.height_mm <= 0:
            return 0.0
        return float((z - self.z_min) / self.height_mm)

    def summary_it(self) -> str:
        punti: list[str] = []
        if self.base_top_z is not None:
            punti.append(f"basetta a {self.ratio_of(self.base_top_z) * 100:.0f}%")
        if self.ankle_z is not None:
            punti.append(f"caviglie a {self.ratio_of(self.ankle_z) * 100:.0f}%")
        if self.crotch_z is not None:
            punti.append(f"biforcazione gambe a {self.ratio_of(self.crotch_z) * 100:.0f}%")
        if self.waist_z is not None:
            punti.append(f"vita a {self.ratio_of(self.waist_z) * 100:.0f}%")
        if self.shoulder_z is not None:
            punti.append(f"spalle a {self.ratio_of(self.shoulder_z) * 100:.0f}%")
        if self.neck_z is not None:
            punti.append(f"collo a {self.ratio_of(self.neck_z) * 100:.0f}%")
        if not punti:
            return "Nessun punto anatomico riconosciuto"
        stile = "Funko Pop" if self.is_funko else ("umanoide" if self.is_humanoid else "oggetto")
        return f"Figura {stile}: " + ", ".join(punti)


def compute_slice_profiles(mesh: trimesh.Trimesh, samples: int = 96) -> list[SliceProfile]:
    """Campiona il modello con sezioni orizzontali equispaziate.

    Args:
        mesh: modello orientato con Z verticale.
        samples: numero di sezioni.

    Returns:
        Elenco di profili dal basso verso l'alto (le sezioni vuote sono escluse).
    """
    if len(mesh.faces) == 0:
        return []

    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = z_max - z_min
    if height <= 1e-9:
        return []

    profiles: list[SliceProfile] = []
    # Evitiamo gli estremi esatti: le sezioni tangenti sono degeneri.
    for z in np.linspace(z_min + height * 0.005, z_max - height * 0.005, samples):
        profile = _profile_at(mesh, float(z), z_min, height)
        if profile is not None:
            profiles.append(profile)
    return profiles


def _profile_at(
    mesh: trimesh.Trimesh, z: float, z_min: float, height: float
) -> SliceProfile | None:
    """Calcola il profilo di una singola sezione."""
    try:
        section = mesh.section(plane_origin=[0.0, 0.0, z], plane_normal=[0.0, 0.0, 1.0])
    except Exception:  # pragma: no cover - sezione degenere
        return None
    if section is None:
        return None

    try:
        planar, _ = section.to_2D()
        polygons = planar.polygons_full
    except Exception:  # pragma: no cover
        return None
    if polygons is None or len(polygons) == 0:
        return None

    area = float(sum(p.area for p in polygons))
    centroids = [(float(p.centroid.x), float(p.centroid.y)) for p in polygons]
    bounds = np.array([p.bounds for p in polygons])  # (n, 4): minx, miny, maxx, maxy
    width_x = float(bounds[:, 2].max() - bounds[:, 0].min())
    width_y = float(bounds[:, 3].max() - bounds[:, 1].min())

    return SliceProfile(
        z=z,
        height_ratio=(z - z_min) / height,
        area_mm2=area,
        islands=len(polygons),
        width_x=width_x,
        width_y=width_y,
        centroids=centroids,
    )


def analyze_anatomy(
    mesh: trimesh.Trimesh, expect_humanoid: bool = True, samples: int = 96
) -> AnatomyAnalysis:
    """Individua i punti di taglio anatomici di una figura.

    Args:
        mesh: modello già scalato e appoggiato sul piano (Z verticale).
        expect_humanoid: se ``False`` si limita a rilevare la basetta.
        samples: risoluzione del campionamento verticale.

    Returns:
        L'analisi con le quote dei punti notevoli.
    """
    analysis = AnatomyAnalysis()
    if len(mesh.faces) == 0:
        return analysis

    z_min, z_max = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    analysis.z_min, analysis.z_max = z_min, z_max
    analysis.height_mm = z_max - z_min
    if analysis.height_mm <= 1e-9:
        return analysis

    profiles = compute_slice_profiles(mesh, samples=samples)
    analysis.profiles = profiles
    if len(profiles) < 8:
        analysis.notes_it.append("Modello troppo piccolo per l'analisi anatomica")
        return analysis

    areas = np.array([p.area_mm2 for p in profiles])
    ratios = np.array([p.height_ratio for p in profiles])
    islands = np.array([p.islands for p in profiles])

    analysis.base_top_z = _detect_base(profiles, areas, ratios)
    analysis.crotch_z = _detect_crotch(profiles, islands, ratios)
    analysis.ankle_z = _detect_ankles(profiles, areas, ratios, analysis.base_top_z)
    analysis.neck_z = _detect_neck(profiles, areas, ratios)
    analysis.shoulder_z = _detect_shoulders(profiles, ratios, analysis.neck_z)
    analysis.waist_z = _detect_waist(profiles, areas, ratios, analysis.crotch_z, analysis.neck_z)
    analysis.arm_split_x = _detect_arm_split(profiles, analysis)

    if analysis.neck_z is not None:
        head_height = z_max - analysis.neck_z
        analysis.head_ratio = float(head_height / analysis.height_mm)
        analysis.is_funko = analysis.head_ratio > 0.32

    analysis.is_humanoid = expect_humanoid and (
        analysis.neck_z is not None or analysis.crotch_z is not None
    )

    logger.debug("Analisi anatomica: %s", analysis.summary_it())
    return analysis


# ---------------------------------------------------------------------------
# Rilevatori dei singoli punti notevoli
# ---------------------------------------------------------------------------


def _detect_base(
    profiles: list[SliceProfile], areas: np.ndarray, ratios: np.ndarray
) -> float | None:
    """Rileva il piano superiore della basetta.

    Una basetta è un disco/parallelepipedo largo e basso: l'area crolla
    bruscamente appena sopra di essa.
    """
    window = (ratios < 0.22) & (ratios > 0.005)
    if window.sum() < 3:
        return None

    indices = np.where(window)[0]
    sub_areas = areas[indices]
    if sub_areas[0] <= 0:
        return None

    # Cerchiamo il calo relativo più marcato fra sezioni consecutive.
    drops = sub_areas[:-1] / np.maximum(sub_areas[1:], 1e-9)
    best = int(np.argmax(drops))
    if drops[best] < 1.8:
        return None
    return float(profiles[indices[best]].z)


def _detect_crotch(
    profiles: list[SliceProfile], islands: np.ndarray, ratios: np.ndarray
) -> float | None:
    """Rileva la quota in cui le due gambe si uniscono nel bacino.

    Salendo dal basso le sezioni mostrano due isole (le gambe); alla
    biforcazione diventano una sola.
    """
    window = (ratios > 0.08) & (ratios < 0.62)
    indices = np.where(window)[0]
    if len(indices) < 4:
        return None

    # Ultima sezione con due o più isole prima di stabilizzarsi a una.
    last_double = None
    for index in indices:
        if islands[index] >= 2:
            last_double = index
        elif last_double is not None and index > last_double + 1:
            break

    if last_double is None:
        return None
    # La biforcazione sta appena sopra l'ultima sezione a due isole.
    next_index = min(last_double + 1, len(profiles) - 1)
    return float(profiles[next_index].z)


def _detect_ankles(
    profiles: list[SliceProfile],
    areas: np.ndarray,
    ratios: np.ndarray,
    base_top_z: float | None,
) -> float | None:
    """Rileva la strozzatura delle caviglie sopra le scarpe."""
    lower = 0.03 if base_top_z is None else 0.04
    window = (ratios > lower) & (ratios < 0.22)
    indices = np.where(window)[0]
    if len(indices) < 3:
        return None

    sub = areas[indices]
    local_min = int(np.argmin(sub))
    # Deve essere un minimo vero, non l'estremo della finestra.
    if local_min in (0, len(sub) - 1):
        return None
    if sub[local_min] > 0.8 * float(sub.max()):
        return None
    return float(profiles[indices[local_min]].z)


def _detect_neck(
    profiles: list[SliceProfile], areas: np.ndarray, ratios: np.ndarray
) -> float | None:
    """Rileva il collo: minimo di area fra il busto e la testa.

    La finestra parte dal 40% dell'altezza per coprire sia le proporzioni
    realistiche (collo verso l'80%) sia quelle Funko (collo verso il 55-60%).
    """
    window = (ratios > 0.40) & (ratios < 0.92)
    indices = np.where(window)[0]
    if len(indices) < 5:
        return None

    sub = areas[indices]
    local_min = int(np.argmin(sub))
    if local_min in (0, len(sub) - 1):
        return None

    # Il collo deve essere sensibilmente più stretto sia del busto sia della testa.
    below = float(sub[:local_min].max())
    above = float(sub[local_min + 1 :].max())
    value = float(sub[local_min])
    if value > 0.72 * min(below, above):
        return None
    return float(profiles[indices[local_min]].z)


def _detect_shoulders(
    profiles: list[SliceProfile], ratios: np.ndarray, neck_z: float | None
) -> float | None:
    """Rileva le spalle: massimo della larghezza X sotto il collo."""
    if neck_z is None:
        window = (ratios > 0.35) & (ratios < 0.75)
    else:
        neck_ratio = None
        for profile in profiles:
            if abs(profile.z - neck_z) < 1e-9:
                neck_ratio = profile.height_ratio
                break
        neck_ratio = neck_ratio if neck_ratio is not None else 0.6
        window = (ratios > neck_ratio - 0.28) & (ratios < neck_ratio)

    indices = np.where(window)[0]
    if len(indices) < 3:
        return None

    widths = np.array([profiles[i].width_x for i in indices])
    return float(profiles[indices[int(np.argmax(widths))]].z)


def _detect_waist(
    profiles: list[SliceProfile],
    areas: np.ndarray,
    ratios: np.ndarray,
    crotch_z: float | None,
    neck_z: float | None,
) -> float | None:
    """Rileva la vita: minimo di area fra biforcazione e collo."""
    low = 0.30 if crotch_z is None else None
    high = 0.70 if neck_z is None else None

    if low is None:
        low = next((p.height_ratio for p in profiles if p.z >= crotch_z), 0.30)
    if high is None:
        high = next((p.height_ratio for p in profiles if p.z >= neck_z), 0.70)
    if high - low < 0.08:
        return None

    window = (ratios > low + 0.02) & (ratios < high - 0.02)
    indices = np.where(window)[0]
    if len(indices) < 3:
        return None

    sub = areas[indices]
    local_min = int(np.argmin(sub))
    return float(profiles[indices[local_min]].z)


def _detect_arm_split(
    profiles: list[SliceProfile], analysis: AnatomyAnalysis
) -> tuple[float, float] | None:
    """Individua i piani verticali che separano le braccia dal busto.

    Nel tratto del torace una sezione tipica mostra tre isole:
    braccio sinistro, torso, braccio destro. I piani di taglio passano a metà
    fra i baricentri delle isole laterali e quello centrale.
    """
    if analysis.shoulder_z is None:
        return None

    candidates = [
        p
        for p in profiles
        if p.islands >= 3 and abs(p.z - analysis.shoulder_z) < analysis.height_mm * 0.18
    ]
    if not candidates:
        return None

    left_gaps: list[float] = []
    right_gaps: list[float] = []
    for profile in candidates:
        xs = sorted(c[0] for c in profile.centroids)
        if len(xs) < 3:
            continue
        left_gaps.append((xs[0] + xs[1]) / 2.0)
        right_gaps.append((xs[-2] + xs[-1]) / 2.0)

    if not left_gaps or not right_gaps:
        return None
    return float(np.median(left_gaps)), float(np.median(right_gaps))


def proportions_for(analysis: AnatomyAnalysis) -> dict[str, tuple[float, float]]:
    """Sceglie il canone di proporzioni più adatto alla figura analizzata."""
    return FUNKO_PROPORTIONS if analysis.is_funko else REALISTIC_PROPORTIONS


def label_by_height(ratio: float, analysis: AnatomyAnalysis) -> PartType:
    """Etichetta una parte in base alla sua quota media, usando i prior.

    Args:
        ratio: quota del baricentro della parte, come frazione dell'altezza.
        analysis: analisi anatomica del modello completo.

    Returns:
        La categoria più probabile per quella fascia di altezza.
    """
    table = proportions_for(analysis)
    mapping = {
        "base": PartType.BASE,
        "shoes": PartType.SHOES,
        "legs": PartType.LEGS,
        "body": PartType.BODY,
        "neck": PartType.BODY,
        "head": PartType.HEAD,
    }

    best_part = PartType.UNKNOWN
    best_score = -1.0
    for key, (low, high) in table.items():
        if low <= ratio <= high:
            center = (low + high) / 2.0
            span = max(1e-6, high - low)
            score = 1.0 - abs(ratio - center) / span
            if score > best_score:
                best_score, best_part = score, mapping.get(key, PartType.UNKNOWN)
    return best_part

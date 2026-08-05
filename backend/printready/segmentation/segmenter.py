"""Segmentazione intelligente: dal modello unico ai pezzi stampabili.

Obiettivo: ottenere una scomposizione in stile *Funko Pop* — testa, capelli,
cappello, occhi, corpo, braccia, mani, gambe, basetta, accessori — con ogni
pezzo chiuso e stampabile separatamente.

Il segmentatore lavora a cascata, dal segnale più affidabile al più debole:

1. **Parti già separate** — se il provider AI o il file sorgente contengono più
   oggetti, quella suddivisione viene rispettata;
2. **Componenti connessi** — gusci fisicamente staccati sono già pezzi distinti
   (armi, occhiali, elementi decorativi);
3. **Tagli anatomici** — piani ricavati dall'analisi delle sezioni orizzontali
   (collo, spalle, vita, biforcazione gambe, caviglie, basetta);
4. **Separazione per colore** — dove il modello porta colori o texture, cluster
   cromatici distinguono capelli, cappello e occhi dalla pelle del volto.

Ogni taglio produce solidi chiusi (``cap=True``), quindi il risultato è già
pronto per la creazione degli incastri.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import numpy as np
import trimesh

from ..ai.semantic import PromptAnalysis
from ..domain.enums import PartType
from ..domain.models import SegmentationSettings
from ..mesh.components import split_components
from ..mesh.io import is_empty
from .anatomy import AnatomyAnalysis, analyze_anatomy
from .labels import (
    LabelHypothesis,
    build_part_name,
    infer_side,
    label_generic_part,
    label_head_subpart,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Part:
    """Un pezzo stampabile con la sua geometria e i suoi metadati."""

    id: str
    name: str
    part_type: PartType
    mesh: trimesh.Trimesh
    side: str = "center"
    confidence: float = 0.5
    color_hex: str | None = None
    reason_it: str = ""
    parent_id: str | None = None

    @property
    def volume_mm3(self) -> float:
        if is_empty(self.mesh):
            return 0.0
        return abs(float(self.mesh.volume)) if self.mesh.is_watertight else 0.0


@dataclass(slots=True)
class SegmentationResult:
    """Esito completo della segmentazione."""

    parts: list[Part] = field(default_factory=list)
    anatomy: AnatomyAnalysis | None = None
    notes_it: list[str] = field(default_factory=list)
    method_it: str = ""

    @property
    def count(self) -> int:
        return len(self.parts)

    def by_type(self, part_type: PartType) -> list[Part]:
        return [p for p in self.parts if p.part_type == part_type]

    def message_it(self) -> str:
        if not self.parts:
            return "Nessuna segmentazione applicata: il modello resta un pezzo unico"
        tipi = ", ".join(dict.fromkeys(p.part_type.label_it for p in self.parts))
        return f"Modello diviso in {len(self.parts)} pezzi ({tipi})"


class Segmenter:
    """Segmentatore configurabile del modello."""

    def __init__(self, settings: SegmentationSettings | None = None) -> None:
        self.settings = settings or SegmentationSettings()

    # -- ingresso pubblico -------------------------------------------------

    def segment(
        self,
        mesh: trimesh.Trimesh,
        prompt: PromptAnalysis,
        pre_split: dict[str, trimesh.Trimesh] | None = None,
    ) -> SegmentationResult:
        """Divide il modello in pezzi stampabili.

        Args:
            mesh: modello completo, già riparato, scalato e appoggiato sul piano.
            prompt: analisi semantica della descrizione dell'utente.
            pre_split: pezzi già separati dal provider AI, se disponibili.

        Returns:
            L'esito con l'elenco dei pezzi etichettati.
        """
        result = SegmentationResult()
        if is_empty(mesh):
            result.notes_it.append("Mesh vuota: segmentazione saltata")
            return result

        if not self.settings.enabled:
            result.parts = [self._make_part(mesh, PartType.UNKNOWN, "Modello completo", 1.0)]
            result.method_it = "segmentazione disattivata"
            return result

        # I tagli con piano richiedono una superficie sana: su una mesh con
        # autointersezioni o bordi aperti la chiusura della sezione (`cap`)
        # fallisce e i pezzi escono non stagni, quindi non stampabili.
        mesh = _consolidate(mesh, result)

        anatomy = analyze_anatomy(mesh, expect_humanoid=prompt.is_humanoid)
        result.anatomy = anatomy
        result.notes_it.append(anatomy.summary_it())

        if pre_split and len(pre_split) > 1:
            result.parts = self._from_pre_split(pre_split, mesh, anatomy, prompt)
            result.method_it = "suddivisione fornita dal generatore AI"
        else:
            result.parts = self._segment_geometric(mesh, anatomy, prompt)
            result.method_it = "tagli anatomici e componenti connessi"

        result.parts = self._post_process(result.parts, mesh, result)
        logger.info("Segmentazione: %s", result.message_it())
        return result

    # -- strategie ---------------------------------------------------------

    def _from_pre_split(
        self,
        pre_split: dict[str, trimesh.Trimesh],
        whole: trimesh.Trimesh,
        anatomy: AnatomyAnalysis,
        prompt: PromptAnalysis,
    ) -> list[Part]:
        """Etichetta i pezzi già separati dal provider."""
        parts: list[Part] = []
        used_names: set[str] = set()

        for index, (name, sub) in enumerate(pre_split.items()):
            if is_empty(sub):
                continue
            hypothesis = label_generic_part(sub, whole, anatomy, prompt)
            side = infer_side(sub, whole)
            label = build_part_name(hypothesis.part_type, side, index, used_names)
            parts.append(
                Part(
                    id=uuid.uuid4().hex,
                    name=label,
                    part_type=hypothesis.part_type,
                    mesh=sub,
                    side=side,
                    confidence=hypothesis.confidence,
                    reason_it=f"pezzo '{name}' fornito dal generatore; {hypothesis.reason_it}",
                    color_hex=dominant_color(sub),
                )
            )
        return parts

    def _segment_geometric(
        self, mesh: trimesh.Trimesh, anatomy: AnatomyAnalysis, prompt: PromptAnalysis
    ) -> list[Part]:
        """Segmentazione completa a partire dalla sola geometria."""
        parts: list[Part] = []
        used_names: set[str] = set()

        # 1) I gusci fisicamente staccati sono già pezzi a sé.
        components = split_components(mesh, only_watertight=False)
        if len(components) > 1:
            volumes = [self._measure(c) for c in components]
            main_index = int(np.argmax(volumes))
            main = components[main_index]
            for index, component in enumerate(components):
                if index == main_index:
                    continue
                hypothesis = label_generic_part(component, mesh, anatomy, prompt)
                side = infer_side(component, mesh)
                parts.append(
                    Part(
                        id=uuid.uuid4().hex,
                        name=build_part_name(hypothesis.part_type, side, index, used_names),
                        part_type=hypothesis.part_type,
                        mesh=component,
                        side=side,
                        confidence=hypothesis.confidence,
                        reason_it=f"guscio separato; {hypothesis.reason_it}",
                        color_hex=dominant_color(component),
                    )
                )
        else:
            main = mesh

        # 2) Tagli anatomici sul corpo principale.
        regions = self._anatomical_cuts(main, anatomy, prompt)
        for region_type, region_mesh in regions:
            if is_empty(region_mesh):
                continue

            # 3) Sotto-segmentazione cromatica della testa (capelli, cappello, occhi).
            if region_type == PartType.HEAD and self.settings.separate_by_color:
                head_parts = self._split_head_by_color(region_mesh, prompt, used_names)
                if head_parts:
                    parts.extend(head_parts)
                    continue

            side = infer_side(region_mesh, main)
            parts.append(
                Part(
                    id=uuid.uuid4().hex,
                    name=build_part_name(region_type, side, len(parts), used_names),
                    part_type=region_type,
                    mesh=region_mesh,
                    side=side,
                    confidence=0.75,
                    reason_it="taglio anatomico",
                    color_hex=dominant_color(region_mesh),
                )
            )

        if not parts:
            parts.append(self._make_part(main, PartType.UNKNOWN, "Modello completo", 0.4))
        return parts

    def _anatomical_cuts(
        self, mesh: trimesh.Trimesh, anatomy: AnatomyAnalysis, prompt: PromptAnalysis
    ) -> list[tuple[PartType, trimesh.Trimesh]]:
        """Applica i piani di taglio ricavati dall'analisi anatomica.

        L'ordine è dall'alto verso il basso: ogni taglio consuma la porzione
        superiore e lascia il resto al taglio successivo.
        """
        regions: list[tuple[PartType, trimesh.Trimesh]] = []

        if not anatomy.is_humanoid:
            return [(PartType.UNKNOWN, mesh)]

        # I tagli orizzontali procedono dall'alto verso il basso; ogni fascia
        # viene isolata prima di applicarvi eventuali tagli verticali, così un
        # piano pensato per le braccia non può intaccare gambe o basetta.
        torso = mesh

        if anatomy.neck_z is not None:
            head, torso = split_by_plane(torso, anatomy.neck_z)
            if not is_empty(head):
                regions.append((PartType.HEAD, head))

        legs_block = None
        if anatomy.crotch_z is not None:
            torso, legs_block = split_by_plane(torso, anatomy.crotch_z)

        base_block = None
        source_for_base = legs_block if legs_block is not None else torso
        if anatomy.base_top_z is not None and not is_empty(source_for_base):
            upper, base_block = split_by_plane(source_for_base, anatomy.base_top_z)
            if legs_block is not None:
                legs_block = upper
            else:
                torso = upper
        if not is_empty(base_block):
            regions.append((PartType.BASE, base_block))

        # Gambe e scarpe: si lavora solo sul blocco inferiore.
        if not is_empty(legs_block):
            shoes_block = None
            if anatomy.ankle_z is not None:
                legs_block, shoes_block = split_by_plane(legs_block, anatomy.ankle_z)
            if not is_empty(shoes_block):
                if prompt.confidence_for(PartType.SHOES) > 0:
                    for piece in self._split_left_right(shoes_block):
                        regions.append((PartType.SHOES, piece))
                else:
                    # Senza scarpe nel prompt il piede resta parte della gamba.
                    legs_block = merge_meshes([legs_block, shoes_block])
            for piece in self._split_left_right(legs_block):
                regions.append((PartType.LEGS, piece))

        # Braccia: piani verticali applicati al solo torso.
        if not is_empty(torso) and self.settings.split_symmetric_pairs:
            # Il torso isolato dà un segnale molto più pulito dell'intera
            # figura; l'analisi globale resta come riserva.
            arm_planes = detect_arm_planes(torso) or anatomy.arm_split_x
            if arm_planes is not None:
                left_x, right_x = arm_planes
                left_arm, torso = split_by_plane(torso, left_x, axis=0, keep_upper=False)
                right_arm, torso = split_by_plane(torso, right_x, axis=0, keep_upper=True)
                for arm in (left_arm, right_arm):
                    if not is_empty(arm):
                        regions.append((PartType.ARMS, arm))

        if not is_empty(torso):
            regions.append((PartType.BODY, torso))
        return regions

    def _split_left_right(self, mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
        """Divide un blocco simmetrico (gambe, scarpe) in destra e sinistra."""
        if is_empty(mesh) or not self.settings.split_symmetric_pairs:
            return [mesh]

        # Se sono già due componenti staccati non serve tagliare.
        components = split_components(mesh, only_watertight=False)
        significant = [c for c in components if self._measure(c) > self._measure(mesh) * 0.15]
        if len(significant) >= 2:
            return significant

        center_x = float(mesh.bounds.mean(axis=0)[0])
        left, right = split_by_plane(mesh, center_x, axis=0, keep_upper=False)
        pieces = [p for p in (left, right) if not is_empty(p)]
        return pieces or [mesh]

    def _split_head_by_color(
        self, head: trimesh.Trimesh, prompt: PromptAnalysis, used_names: set[str]
    ) -> list[Part]:
        """Separa capelli, cappello, occhi e barba usando i cluster di colore."""
        clusters = color_clusters(head, max_clusters=min(6, self.settings.max_parts))
        if len(clusters) < 2:
            return []

        parts: list[Part] = []
        for face_mask, color in clusters:
            sub = submesh_from_faces(head, face_mask)
            if is_empty(sub):
                continue
            if self._measure(sub) < self._measure(head) * 0.01:
                continue

            hypothesis = label_head_subpart(sub, head, prompt, color)
            side = infer_side(sub, head)
            parts.append(
                Part(
                    id=uuid.uuid4().hex,
                    name=build_part_name(hypothesis.part_type, side, len(parts), used_names),
                    part_type=hypothesis.part_type,
                    mesh=sub,
                    side=side,
                    confidence=hypothesis.confidence,
                    reason_it=f"cluster cromatico: {hypothesis.reason_it}",
                    color_hex=color,
                )
            )

        # Se la separazione cromatica ha prodotto un solo pezzo utile la scartiamo.
        return parts if len(parts) >= 2 else []

    # -- rifinitura --------------------------------------------------------

    def _post_process(
        self, parts: list[Part], whole: trimesh.Trimesh, result: SegmentationResult
    ) -> list[Part]:
        """Fonde i frammenti, applica i limiti e ordina i pezzi."""
        if not parts:
            return parts

        total = self._measure(whole)
        threshold = total * self.settings.min_part_volume_ratio

        keep: list[Part] = []
        tiny: list[Part] = []
        for part in parts:
            if self._measure(part.mesh) < threshold:
                tiny.append(part)
            else:
                keep.append(part)

        if tiny:
            if self.settings.merge_tiny_parts and keep:
                # Ogni frammento viene fuso nel pezzo più vicino.
                for fragment in tiny:
                    host = min(keep, key=lambda p: _center_distance(p.mesh, fragment.mesh))
                    host.mesh = merge_meshes([host.mesh, fragment.mesh])
                result.notes_it.append(
                    f"{len(tiny)} frammenti minori fusi nei pezzi adiacenti"
                )
            else:
                result.notes_it.append(f"{len(tiny)} frammenti minori scartati")

        if len(keep) > self.settings.max_parts:
            keep.sort(key=lambda p: -self._measure(p.mesh))
            excess = keep[self.settings.max_parts :]
            keep = keep[: self.settings.max_parts]
            for fragment in excess:
                host = min(keep, key=lambda p: _center_distance(p.mesh, fragment.mesh))
                host.mesh = merge_meshes([host.mesh, fragment.mesh])
            result.notes_it.append(
                f"Limite di {self.settings.max_parts} pezzi applicato: "
                f"{len(excess)} pezzi accorpati"
            )

        # Ogni pezzo deve uscire da qui stampabile: un taglio può lasciare
        # bordi aperti o frammenti, e un pezzo non stagno non ha volume, non
        # può ricevere incastri e viene rifiutato dallo slicer.
        riparati = 0
        for part in keep:
            if is_empty(part.mesh) or part.mesh.is_watertight:
                continue
            fixed = _repair_part(part.mesh)
            if fixed is not None:
                part.mesh = fixed
                riparati += 1
        if riparati:
            result.notes_it.append(f"{riparati} pezzi richiusi dopo il taglio")

        # Ordine di presentazione: dall'alto verso il basso, come si assembla.
        keep.sort(key=lambda p: -float(p.mesh.bounds.mean(axis=0)[2]))
        return keep

    def _measure(self, mesh: trimesh.Trimesh) -> float:
        """Volume robusto usato per confronti relativi."""
        if is_empty(mesh):
            return 0.0
        if mesh.is_watertight:
            return abs(float(mesh.volume))
        return float(np.prod(np.maximum(mesh.extents, 1e-9)))

    def _make_part(
        self, mesh: trimesh.Trimesh, part_type: PartType, name: str, confidence: float
    ) -> Part:
        return Part(
            id=uuid.uuid4().hex,
            name=name,
            part_type=part_type,
            mesh=mesh,
            confidence=confidence,
            color_hex=dominant_color(mesh),
        )


# ---------------------------------------------------------------------------
# Operazioni geometriche di supporto
# ---------------------------------------------------------------------------


def split_by_plane(
    mesh: trimesh.Trimesh, position: float, axis: int = 2, keep_upper: bool = True
) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Taglia la mesh con un piano ortogonale a un asse, chiudendo le sezioni.

    Args:
        mesh: mesh da tagliare.
        position: coordinata del piano lungo ``axis``.
        axis: 0 = X, 1 = Y, 2 = Z.
        keep_upper: determina quale metà viene restituita per prima.

    Returns:
        ``(porzione_superiore, porzione_inferiore)`` rispetto all'asse scelto.
        Se il taglio non è applicabile la mesh originale finisce nella seconda
        posizione e la prima è vuota.
    """
    from ..mesh.io import empty_mesh

    if is_empty(mesh):
        return empty_mesh(), mesh

    lower_bound = float(mesh.bounds[0][axis])
    upper_bound = float(mesh.bounds[1][axis])
    span = upper_bound - lower_bound
    # Un piano troppo vicino a un estremo produrrebbe un pezzo inconsistente.
    if span <= 1e-6 or not (lower_bound + span * 0.02 < position < upper_bound - span * 0.02):
        return empty_mesh(), mesh

    normal = np.zeros(3)
    normal[axis] = 1.0
    origin = np.zeros(3)
    origin[axis] = position

    upper = _cut_half(mesh, normal, origin)
    lower = _cut_half(mesh, -normal, origin)

    if keep_upper:
        return upper, lower
    return lower, upper


def _cut_half(
    mesh: trimesh.Trimesh, normal: np.ndarray, origin: np.ndarray
) -> trimesh.Trimesh:
    """Ritaglia il semispazio indicato dalla normale, garantendo un solido chiuso.

    ``slice_mesh_plane`` è veloce ma chiude la sezione con una semplice
    triangolazione del contorno: dove la sezione è frastagliata o la mesh ha
    autointersezioni, la chiusura non riesce e il pezzo esce aperto — quindi
    senza volume, non stampabile e incapace di ricevere incastri.

    In quel caso si ripiega sull'**intersezione booleana con una scatola**
    che rappresenta il semispazio: è più costosa ma il motore esatto restituisce
    sempre un solido valido.
    """
    from ..mesh.booleans import boolean_intersection
    from ..mesh.io import empty_mesh

    try:
        result = trimesh.intersections.slice_mesh_plane(
            mesh, plane_normal=normal, plane_origin=origin, cap=True
        )
    except Exception as exc:
        logger.debug("Taglio rapido non riuscito (%s): passo alla booleana", exc)
        result = None

    if result is not None and not is_empty(result) and result.is_watertight:
        return result

    half_space = _half_space_box(mesh, normal, origin)
    if half_space is None:
        return result if result is not None else empty_mesh()

    solid = boolean_intersection([mesh, half_space])
    if solid is not None and not is_empty(solid):
        return solid
    return result if result is not None else empty_mesh()


def _half_space_box(
    mesh: trimesh.Trimesh, normal: np.ndarray, origin: np.ndarray
) -> trimesh.Trimesh | None:
    """Scatola che copre il semispazio dalla parte indicata dalla normale.

    È dimensionata sul doppio della diagonale del modello, così da contenerlo
    interamente da quel lato del piano senza tagliarne i bordi.
    """
    extents = mesh.extents
    if extents is None or float(np.max(extents)) <= 0:
        return None

    size = float(np.linalg.norm(extents)) * 2.0
    box = trimesh.creation.box(extents=[size, size, size])
    # La scatola nasce centrata: la si sposta perché una faccia coincida con il
    # piano di taglio e il volume si sviluppi lungo la normale.
    box.apply_translation([0.0, 0.0, size / 2.0])

    transform = trimesh.geometry.align_vectors(np.array([0.0, 0.0, 1.0]), np.asarray(normal, dtype=np.float64))
    if transform is None:  # pragma: no cover - normale già allineata
        transform = np.eye(4)
    matrix = np.array(transform, dtype=np.float64)
    matrix[:3, 3] = np.asarray(origin, dtype=np.float64)
    box.apply_transform(matrix)
    return box


def _consolidate(mesh: trimesh.Trimesh, result: SegmentationResult) -> trimesh.Trimesh:
    """Rende la mesh adatta ai tagli, con un intervento minimo.

    Serve solo a garantire che il modello sia chiuso: i tagli sanno già
    gestire da soli le sezioni difficili (vedi :func:`_cut_half`), quindi qui
    non si rielabora la superficie — un'operazione globale su una mesh da
    centomila triangoli costerebbe più dell'intera segmentazione e ne
    altererebbe le proporzioni.
    """
    from ..mesh.repair import auto_repair

    if is_empty(mesh) or mesh.is_watertight:
        return mesh

    repaired, report = auto_repair(mesh, max_passes=1)
    if not is_empty(repaired):
        if report.actions_it:
            result.notes_it.append("Modello richiuso prima del taglio")
        return repaired
    return mesh


def _repair_part(mesh: trimesh.Trimesh) -> trimesh.Trimesh | None:
    """Richiude un pezzo rimasto aperto dopo il taglio.

    Returns:
        La mesh riparata, oppure ``None`` se la riparazione non è servita a
        nulla (in tal caso è meglio conservare l'originale).
    """
    from ..mesh.holes import close_holes
    from ..mesh.repair import auto_repair

    closed, _ = close_holes(mesh)
    if not is_empty(closed) and closed.is_watertight:
        return closed

    repaired, _ = auto_repair(mesh, max_passes=2)
    if not is_empty(repaired) and repaired.is_watertight:
        return repaired

    logger.debug("Pezzo non richiudibile: resta non stagno")
    return None


def detect_arm_planes(
    torso: trimesh.Trimesh,
    samples: int = 60,
    max_arm_fraction: float = 0.35,
    min_arm_volume_ratio: float = 0.01,
    max_arm_volume_ratio: float = 0.30,
) -> tuple[float, float] | None:
    """Individua i due piani verticali che staccano le braccia dal torso.

    Il segnale è il profilo dell'area delle sezioni verticali lungo X. Le
    braccia si manifestano in due modi, entrambi gestiti qui:

    * **braccia staccate dal busto** — fra braccio e busto l'area scende a zero
      o quasi: c'è una vera gola;
    * **braccia attaccate alle spalle** (il caso più comune) — l'area disegna un
      *gradino*: sottile dove c'è solo il braccio, larga dove inizia il busto.
      Un cercatore di minimi locali non lo vedrebbe, mentre il salto di area sì.

    Il candidato viene infine **validato tagliando davvero**: il pezzo esterno
    deve avere un volume plausibile per un braccio. Questo evita di amputare un
    fianco quando le braccia non ci sono.

    Args:
        torso: blocco del busto (con le braccia ancora attaccate).
        samples: numero di sezioni lungo X.
        max_arm_fraction: un braccio non può estendersi oltre questa frazione
            della larghezza del torso.
        min_arm_volume_ratio: volume minimo del pezzo esterno, sul totale.
        max_arm_volume_ratio: volume massimo del pezzo esterno, sul totale.

    Returns:
        ``(x_sinistra, x_destra)`` oppure ``None`` se non si riconoscono braccia.
    """
    if is_empty(torso):
        return None

    x_min, x_max = float(torso.bounds[0][0]), float(torso.bounds[1][0])
    width = x_max - x_min
    if width <= 1e-6:
        return None

    positions = np.linspace(x_min + width * 0.02, x_max - width * 0.02, samples)
    areas = np.zeros(samples, dtype=np.float64)

    for index, x in enumerate(positions):
        try:
            section = torso.section(plane_origin=[float(x), 0.0, 0.0], plane_normal=[1.0, 0.0, 0.0])
            if section is None:
                continue
            planar, _ = section.to_2D()
            polygons = planar.polygons_full
            if polygons:
                areas[index] = float(sum(p.area for p in polygons))
        except Exception:  # pragma: no cover - sezione degenere
            continue

    peak = float(areas.max())
    if peak <= 0:
        return None

    limit = max(3, int(samples * max_arm_fraction))
    if limit * 2 >= samples:
        return None

    # Salto di area fra sezioni consecutive: positivo = si entra nel busto.
    steps = np.diff(areas)
    # Soglia: un gradino vero vale almeno un quarto della sezione massima.
    min_step = peak * 0.25

    left_window = steps[:limit]
    right_window = steps[samples - 1 - limit :]
    if len(left_window) == 0 or len(right_window) == 0:
        return None

    left_best = int(np.argmax(left_window))
    right_best = int(np.argmin(right_window))
    if float(left_window[left_best]) < min_step or float(-right_window[right_best]) < min_step:
        return None

    left_x = float(positions[left_best])
    right_x = float(positions[samples - 1 - limit + right_best + 1])
    if left_x >= right_x:
        return None

    # Validazione: i pezzi esterni devono avere il volume di un braccio.
    total = _robust_volume(torso)
    if total <= 0:
        return None

    left_arm, _ = split_by_plane(torso, left_x, axis=0, keep_upper=False)
    right_arm, _ = split_by_plane(torso, right_x, axis=0, keep_upper=True)
    for arm in (left_arm, right_arm):
        if is_empty(arm):
            return None
        ratio = _robust_volume(arm) / total
        if not (min_arm_volume_ratio <= ratio <= max_arm_volume_ratio):
            logger.debug("Piano braccio scartato: volume relativo %.3f fuori intervallo", ratio)
            return None

    return left_x, right_x


def _robust_volume(mesh: trimesh.Trimesh) -> float:
    """Volume se la mesh è chiusa, altrimenti volume del bounding box."""
    if is_empty(mesh):
        return 0.0
    if mesh.is_watertight:
        return abs(float(mesh.volume))
    return float(np.prod(np.maximum(mesh.extents, 1e-9)))


def merge_meshes(meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    """Concatena più mesh in una sola (senza fusione booleana)."""
    valid = [m for m in meshes if not is_empty(m)]
    if not valid:
        from ..mesh.io import empty_mesh

        return empty_mesh()
    if len(valid) == 1:
        return valid[0]
    return trimesh.util.concatenate(valid)


def submesh_from_faces(mesh: trimesh.Trimesh, face_mask: np.ndarray) -> trimesh.Trimesh:
    """Estrae la sottomesh definita da una maschera di facce, chiudendo i bordi."""
    from ..mesh.holes import close_holes
    from ..mesh.io import empty_mesh

    if not face_mask.any():
        return empty_mesh()

    try:
        sub = mesh.submesh([np.where(face_mask)[0]], append=True, repair=False)
    except Exception as exc:  # pragma: no cover
        logger.warning("Estrazione sottomesh fallita: %s", exc)
        return empty_mesh()

    if is_empty(sub):
        return empty_mesh()
    closed, _ = close_holes(sub)
    return closed


def vertex_colors(mesh: trimesh.Trimesh) -> np.ndarray | None:
    """Estrae i colori per vertice, convertendo la texture se necessario.

    Returns:
        Array ``(N, 3)`` in [0, 255] oppure ``None`` se il modello non ha colori
        significativi (tutti uguali = colore di default di trimesh).
    """
    visual = getattr(mesh, "visual", None)
    if visual is None:
        return None

    colors = None
    try:
        if hasattr(visual, "to_color"):
            colors = np.asarray(visual.to_color().vertex_colors)
        elif hasattr(visual, "vertex_colors"):
            colors = np.asarray(visual.vertex_colors)
    except Exception as exc:  # pragma: no cover
        logger.debug("Colori per vertice non disponibili: %s", exc)
        return None

    if colors is None or colors.size == 0 or len(colors) != len(mesh.vertices):
        return None

    rgb = colors[:, :3].astype(np.float64)
    # Un modello senza colori reali ha tutti i vertici identici.
    if float(rgb.std(axis=0).max()) < 4.0:
        return None
    return rgb


def color_clusters(
    mesh: trimesh.Trimesh, max_clusters: int = 6, min_face_ratio: float = 0.01
) -> list[tuple[np.ndarray, str]]:
    """Raggruppa le facce per colore dominante.

    Usa k-means in numpy (nessuna dipendenza da scikit-learn) sul colore medio
    di ogni faccia, poi scarta i cluster troppo piccoli.

    Returns:
        Lista di ``(maschera_facce, colore_hex)``.
    """
    colors = vertex_colors(mesh)
    if colors is None or is_empty(mesh):
        return []

    face_colors = colors[mesh.faces].mean(axis=1)  # (F, 3)
    if len(face_colors) < max_clusters * 4:
        return []

    labels, centers = _kmeans(face_colors, k=max_clusters)
    clusters: list[tuple[np.ndarray, str]] = []
    minimum = max(4, int(len(face_colors) * min_face_ratio))

    for index in range(len(centers)):
        mask = labels == index
        if int(mask.sum()) < minimum:
            continue
        clusters.append((mask, rgb_to_hex(centers[index])))

    return clusters


def _kmeans(
    data: np.ndarray, k: int, iterations: int = 25, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """K-means con inizializzazione k-means++ semplificata."""
    rng = np.random.default_rng(seed)
    k = int(min(k, len(data)))
    if k <= 1:
        return np.zeros(len(data), dtype=np.int64), data.mean(axis=0, keepdims=True)

    # k-means++: il primo centro a caso, gli altri lontani da quelli scelti.
    centers = [data[rng.integers(len(data))]]
    for _ in range(k - 1):
        distances = np.min(
            np.linalg.norm(data[:, None, :] - np.array(centers)[None, :, :], axis=2), axis=1
        )
        total = float(distances.sum())
        if total <= 0:
            centers.append(data[rng.integers(len(data))])
            continue
        probabilities = distances / total
        centers.append(data[rng.choice(len(data), p=probabilities)])

    centers_array = np.array(centers, dtype=np.float64)
    labels = np.zeros(len(data), dtype=np.int64)

    for _ in range(iterations):
        distances = np.linalg.norm(data[:, None, :] - centers_array[None, :, :], axis=2)
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for index in range(len(centers_array)):
            members = data[labels == index]
            if len(members):
                centers_array[index] = members.mean(axis=0)

    return labels, centers_array


def dominant_color(mesh: trimesh.Trimesh) -> str | None:
    """Colore medio del pezzo in formato HEX, se il modello ha colori."""
    colors = vertex_colors(mesh)
    if colors is None:
        return None
    return rgb_to_hex(colors.mean(axis=0))


def rgb_to_hex(rgb: np.ndarray) -> str:
    """Converte una terna RGB 0-255 in stringa ``#rrggbb``."""
    values = np.clip(np.asarray(rgb, dtype=np.float64), 0, 255).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*values[:3])


def _center_distance(a: trimesh.Trimesh, b: trimesh.Trimesh) -> float:
    """Distanza fra i baricentri dei bounding box di due mesh."""
    if is_empty(a) or is_empty(b):
        return float("inf")
    return float(np.linalg.norm(a.bounds.mean(axis=0) - b.bounds.mean(axis=0)))

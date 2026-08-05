"""Pianificazione e applicazione degli incastri fra i pezzi.

Il pianificatore risponde a tre domande, in quest'ordine:

1. **Quali pezzi si toccano?** — coppie con una superficie di contatto reale,
   individuate misurando la distanza fra le superfici.
2. **Dove va l'incastro?** — nel baricentro dell'area di contatto, lungo la
   normale media di quella superficie.
3. **Quanto deve essere grande?** — ricavato dal raggio utile dell'area di
   contatto e dalla profondità di materiale disponibile nel pezzo femmina.

Applicati i booleani, ogni pezzo esce con la sua spina o il suo alloggiamento e
il modello si assembla senza colla.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import numpy as np
import trimesh

from ..domain.enums import JoineryType, PartType
from ..domain.models import ConnectorInfo, JoinerySettings, PrinterProfile
from ..mesh.booleans import boolean_difference, boolean_union
from ..mesh.io import is_empty
from ..segmentation.segmenter import Part
from .connectors import build_pin, magnet_socket, socket_for
from .tolerance import ConnectorDimensions, magnet_specification, size_connector

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ContactInterface:
    """Superficie di contatto fra due pezzi."""

    male_part_id: str
    female_part_id: str
    point: np.ndarray
    direction: np.ndarray
    area_mm2: float
    radius_mm: float
    available_depth_mm: float
    is_vertical: bool

    def describe_it(self) -> str:
        return (
            f"contatto di {self.area_mm2:.1f} mm² "
            f"(raggio utile {self.radius_mm:.1f} mm, profondità {self.available_depth_mm:.1f} mm)"
        )


@dataclass(slots=True)
class JoineryResult:
    """Esito della creazione degli incastri."""

    parts: list[Part] = field(default_factory=list)
    connectors: list[ConnectorInfo] = field(default_factory=list)
    skipped_it: list[str] = field(default_factory=list)
    notes_it: list[str] = field(default_factory=list)

    def message_it(self) -> str:
        if not self.connectors:
            return "Nessun incastro creato: i pezzi non presentano superfici di contatto adatte"
        tipi = ", ".join(dict.fromkeys(c.joint_type.label_it for c in self.connectors))
        return f"Creati {len(self.connectors)} incastri ({tipi})"


class JoineryPlanner:
    """Crea gli incastri fra i pezzi segmentati."""

    def __init__(
        self,
        settings: JoinerySettings | None = None,
        printer: PrinterProfile | None = None,
    ) -> None:
        self.settings = settings or JoinerySettings()
        self.printer = printer or PrinterProfile()

    # -- ingresso pubblico -------------------------------------------------

    def apply(self, parts: list[Part]) -> JoineryResult:
        """Individua le interfacce e applica spine e sedi a tutti i pezzi.

        Args:
            parts: pezzi segmentati (le mesh vengono sostituite con quelle
                modificate; l'oggetto ``Part`` è aggiornato sul posto).

        Returns:
            L'esito con i connettori creati e le eventuali rinunce motivate.
        """
        result = JoineryResult(parts=parts)
        if not self.settings.enabled or self.settings.joint_type == JoineryType.NONE:
            result.notes_it.append("Creazione incastri disattivata nelle impostazioni")
            return result
        if len(parts) < 2:
            result.notes_it.append("Un solo pezzo: nessun incastro necessario")
            return result

        interfaces = self.find_interfaces(parts)
        if not interfaces:
            result.notes_it.append(
                "Nessuna superficie di contatto rilevata fra i pezzi: "
                "verificare che la segmentazione non li abbia allontanati"
            )
            return result

        by_id = {part.id: part for part in parts}

        for interface in interfaces:
            male = by_id.get(interface.male_part_id)
            female = by_id.get(interface.female_part_id)
            if male is None or female is None:
                continue

            dimensions = size_connector(
                self.settings,
                self.printer,
                contact_radius_mm=interface.radius_mm,
                available_depth_mm=interface.available_depth_mm,
                vertical=interface.is_vertical,
            )

            if not self._is_feasible(interface, dimensions, result, male, female):
                continue

            connector = self._apply_single(male, female, interface, dimensions)
            if connector is not None:
                result.connectors.append(connector)
                male.mesh._cache.clear()
                female.mesh._cache.clear()

                if self.settings.anti_rotation and self._has_room_for_second(
                    interface, dimensions
                ):
                    extra = self._apply_anti_rotation(male, female, interface, dimensions)
                    if extra is not None:
                        result.connectors.append(extra)

        logger.info("Incastri: %s", result.message_it())
        return result

    # -- individuazione delle interfacce -----------------------------------

    def find_interfaces(
        self, parts: list[Part], contact_epsilon_mm: float = 0.6
    ) -> list[ContactInterface]:
        """Trova tutte le coppie di pezzi con una superficie di contatto reale.

        Args:
            parts: pezzi da esaminare.
            contact_epsilon_mm: distanza sotto la quale due superfici si
                considerano a contatto (i tagli con piano lasciano superfici
                coincidenti, quindi la soglia può essere stretta).

        Returns:
            Elenco di interfacce, dalla più estesa alla meno estesa.
        """
        interfaces: list[ContactInterface] = []

        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                a, b = parts[i], parts[j]
                if is_empty(a.mesh) or is_empty(b.mesh):
                    continue
                if not _bounds_touch(a.mesh, b.mesh, contact_epsilon_mm):
                    continue

                interface = self._build_interface(a, b, contact_epsilon_mm)
                if interface is not None:
                    interfaces.append(interface)

        interfaces.sort(key=lambda x: -x.area_mm2)
        return interfaces

    def _build_interface(
        self, a: Part, b: Part, epsilon: float
    ) -> ContactInterface | None:
        """Calcola punto, direzione e dimensioni dell'area di contatto fra due pezzi."""
        centers = np.asarray(a.mesh.triangles_center, dtype=np.float64)
        touching = _touching_faces(centers, b.mesh, epsilon)
        if touching is None or not touching.any():
            return None

        areas = np.asarray(a.mesh.area_faces, dtype=np.float64)[touching]
        total_area = float(areas.sum())
        if total_area < 1.0:  # meno di 1 mm²: contatto puntiforme, inutilizzabile
            return None

        points = centers[touching]
        weights = areas / total_area
        contact_point = (points * weights[:, None]).sum(axis=0)

        normals = np.asarray(a.mesh.face_normals, dtype=np.float64)[touching]
        direction = (normals * weights[:, None]).sum(axis=0)
        norm = float(np.linalg.norm(direction))
        if norm < 1e-6:
            # Normali che si annullano: contatto su superfici opposte, non piano.
            return None
        direction = direction / norm

        # La direzione deve puntare da A verso B.
        towards_b = np.asarray(b.mesh.bounds.mean(axis=0)) - contact_point
        if float(np.dot(direction, towards_b)) < 0:
            direction = -direction

        male, female = self._assign_roles(a, b)
        if male is b:
            direction = -direction

        # Raggio utile attorno al punto scelto. Il solo cerchio equivalente
        # all'area sovrastima quando il contatto è allungato o frammentato
        # (due impronte separate), quindi lo limitiamo con la dispersione
        # effettiva dei punti di contatto misurata sul piano dell'interfaccia.
        planar = points - contact_point
        planar = planar - np.outer(planar @ direction, direction)
        spread = np.linalg.norm(planar, axis=1)
        radius = float(
            min(
                np.sqrt(total_area / np.pi) * 0.75,
                np.percentile(spread, 60) if len(spread) > 4 else np.sqrt(total_area / np.pi) * 0.75,
            )
        )

        # `direction` punta sempre dal maschio verso la femmina, quindi è già
        # la direzione da seguire per scavare dentro il pezzo femmina.
        depth = self._available_depth(female.mesh, contact_point, direction)

        return ContactInterface(
            male_part_id=male.id,
            female_part_id=female.id,
            point=contact_point,
            direction=direction,
            area_mm2=total_area,
            radius_mm=radius,
            available_depth_mm=depth,
            is_vertical=abs(float(direction[2])) > 0.7,
        )

    def _assign_roles(self, a: Part, b: Part) -> tuple[Part, Part]:
        """Decide quale pezzo porta la spina e quale l'alloggiamento.

        Criteri, in ordine:

        1. la **basetta** porta sempre la spina (il modello si infila sopra);
        2. per pezzi impilati verticalmente la spina sta sul pezzo **inferiore**,
           così il pezzo superiore si cala dall'alto durante il montaggio;
        3. altrimenti la spina sta sul pezzo **più grande**, che ha più materiale
           attorno alla radice e regge meglio lo sforzo.
        """
        if a.part_type == PartType.BASE:
            return a, b
        if b.part_type == PartType.BASE:
            return b, a

        a_z = float(a.mesh.bounds.mean(axis=0)[2])
        b_z = float(b.mesh.bounds.mean(axis=0)[2])
        if abs(a_z - b_z) > 1.0:
            return (a, b) if a_z < b_z else (b, a)

        a_volume = _robust_volume(a.mesh)
        b_volume = _robust_volume(b.mesh)
        return (a, b) if a_volume >= b_volume else (b, a)

    def _available_depth(
        self, mesh: trimesh.Trimesh, point: np.ndarray, direction: np.ndarray
    ) -> float:
        """Spessore di materiale disponibile per scavare la sede.

        Lancia un raggio dal punto di contatto verso l'interno del pezzo e
        misura dove esce.
        """
        if is_empty(mesh):
            return 0.0

        direction = np.asarray(direction, dtype=np.float64)
        origin = np.asarray(point, dtype=np.float64) + direction * 1e-3
        try:
            locations, _, _ = mesh.ray.intersects_location(
                ray_origins=origin.reshape(1, 3),
                ray_directions=direction.reshape(1, 3),
                multiple_hits=True,
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("Ray casting per la profondità fallito: %s", exc)
            return float(np.min(mesh.extents)) * 0.5

        if len(locations) == 0:
            return float(np.min(mesh.extents)) * 0.5

        distances = np.linalg.norm(locations - origin, axis=1)
        # Il punto di partenza giace sulla superficie di taglio condivisa fra i
        # due pezzi: il primo impatto è quella stessa superficie e va ignorato,
        # altrimenti la profondità risulterebbe sempre nulla.
        useful = distances[distances > 0.05]
        if len(useful) == 0:
            return 0.0
        return float(useful.min())

    # -- verifica di fattibilità -------------------------------------------

    def _is_feasible(
        self,
        interface: ContactInterface,
        dimensions: ConnectorDimensions,
        result: JoineryResult,
        male: Part,
        female: Part,
    ) -> bool:
        """Verifica che l'incastro stia fisicamente nei due pezzi."""
        min_wall = self.printer.min_printable_wall

        if interface.radius_mm < dimensions.male_diameter_mm / 2.0 + min_wall:
            result.skipped_it.append(
                f"{male.name} ↔ {female.name}: superficie di contatto troppo piccola "
                f"per una spina Ø{dimensions.male_diameter_mm:.1f} mm"
            )
            return False

        if interface.available_depth_mm < dimensions.socket_depth_mm + min_wall:
            result.skipped_it.append(
                f"{male.name} ↔ {female.name}: spessore insufficiente in «{female.name}» "
                f"per una sede profonda {dimensions.socket_depth_mm:.1f} mm"
            )
            return False

        return True

    def _has_room_for_second(
        self, interface: ContactInterface, dimensions: ConnectorDimensions
    ) -> bool:
        """C'è spazio per una seconda spina di antirotazione?"""
        needed = dimensions.female_diameter_mm * 2.2 + self.printer.min_printable_wall * 2
        return interface.radius_mm >= needed

    # -- applicazione ------------------------------------------------------

    def _apply_single(
        self,
        male: Part,
        female: Part,
        interface: ContactInterface,
        dimensions: ConnectorDimensions,
        offset: np.ndarray | None = None,
    ) -> ConnectorInfo | None:
        """Applica un singolo incastro modificando le due mesh."""
        point = interface.point if offset is None else interface.point + offset
        direction = interface.direction
        joint_type = self.settings.joint_type

        if joint_type == JoineryType.MAGNET:
            return self._apply_magnet(male, female, point, direction, dimensions)

        pin = build_pin(joint_type, point, direction, dimensions)
        if pin is None or is_empty(pin):
            logger.warning("Spina non generata per %s ↔ %s", male.name, female.name)
            return None

        cavity = socket_for(joint_type, point, direction, dimensions)
        if is_empty(cavity):
            return None

        # La cavità va scavata prima di unire la spina: se i due pezzi
        # condividono la stessa superficie, l'ordine evita interferenze.
        new_female = boolean_difference(female.mesh, [cavity])
        new_male = boolean_union([male.mesh, pin])

        if is_empty(new_male) or is_empty(new_female):
            logger.warning("Operazione booleana fallita per %s ↔ %s", male.name, female.name)
            return None

        male.mesh = new_male
        female.mesh = new_female

        connector = ConnectorInfo(
            id=uuid.uuid4().hex,
            joint_type=joint_type,
            male_part_id=male.id,
            female_part_id=female.id,
            position=tuple(float(v) for v in point),
            direction=tuple(float(v) for v in direction),
            diameter_mm=dimensions.male_diameter_mm,
            length_mm=dimensions.length_mm,
            tolerance_mm=dimensions.tolerance_mm,
        )
        logger.debug(
            "Incastro %s fra %s e %s: %s",
            joint_type.value,
            male.name,
            female.name,
            dimensions.describe_it(),
        )
        return connector

    def _apply_magnet(
        self,
        male: Part,
        female: Part,
        point: np.ndarray,
        direction: np.ndarray,
        dimensions: ConnectorDimensions,
    ) -> ConnectorInfo | None:
        """Scava l'alloggiamento del magnete su entrambi i pezzi."""
        male_cavity = magnet_socket(
            point,
            direction,
            self.settings.magnet_diameter_mm + dimensions.tolerance_mm,
            self.settings.magnet_height_mm,
            self.settings.magnet_recess_mm,
            inward=True,
        )
        female_cavity = magnet_socket(
            point,
            direction,
            self.settings.magnet_diameter_mm + dimensions.tolerance_mm,
            self.settings.magnet_height_mm,
            self.settings.magnet_recess_mm,
            inward=False,
        )

        new_male = boolean_difference(male.mesh, [male_cavity])
        new_female = boolean_difference(female.mesh, [female_cavity])
        if is_empty(new_male) or is_empty(new_female):
            return None

        male.mesh = new_male
        female.mesh = new_female

        return ConnectorInfo(
            id=uuid.uuid4().hex,
            joint_type=JoineryType.MAGNET,
            male_part_id=male.id,
            female_part_id=female.id,
            position=tuple(float(v) for v in point),
            direction=tuple(float(v) for v in direction),
            diameter_mm=self.settings.magnet_diameter_mm,
            length_mm=self.settings.magnet_height_mm,
            tolerance_mm=dimensions.tolerance_mm,
            magnet_spec=magnet_specification(self.settings),
        )

    def _apply_anti_rotation(
        self,
        male: Part,
        female: Part,
        interface: ContactInterface,
        dimensions: ConnectorDimensions,
    ) -> ConnectorInfo | None:
        """Aggiunge una seconda spina più piccola per impedire la rotazione."""
        perpendicular = _perpendicular_to(interface.direction)
        distance = dimensions.female_diameter_mm * 1.6
        offset = perpendicular * distance

        smaller = ConnectorDimensions(
            male_diameter_mm=dimensions.male_diameter_mm * 0.6,
            female_diameter_mm=dimensions.female_diameter_mm * 0.6,
            length_mm=dimensions.length_mm * 0.7,
            socket_depth_mm=dimensions.socket_depth_mm * 0.7,
            tolerance_mm=dimensions.tolerance_mm,
            taper_ratio=dimensions.taper_ratio,
            chamfer_mm=dimensions.chamfer_mm,
        )
        return self._apply_single(male, female, interface, smaller, offset=offset)


# ---------------------------------------------------------------------------
# Utilità geometriche
# ---------------------------------------------------------------------------


def _touching_faces(
    points: np.ndarray, other: trimesh.Trimesh, epsilon: float, refine_limit: int = 4000
) -> np.ndarray | None:
    """Maschera dei punti che si trovano entro ``epsilon`` dalla superficie di ``other``.

    Il calcolo esatto punto-superficie (``nearest.on_surface``) costa troppo per
    decine di migliaia di punti moltiplicati per tutte le coppie di pezzi: su un
    modello reale l'operazione dominerebbe l'intera pipeline.

    Qui si procede in due fasi:

    1. **selezione** con un KD-tree sui vertici dell'altro pezzo — molto veloce
       e, poiché i pezzi nascono da tagli con piano, già esatta sulle superfici
       di contatto (i vertici coincidono). La soglia è allargata per non perdere
       candidati dove la maglia è rada;
    2. **raffinamento** con la distanza esatta punto-superficie, applicata ai
       soli candidati sopravvissuti alla prima fase.

    Returns:
        Maschera booleana lunga quanto ``points``, oppure ``None`` in caso di
        errore nel calcolo di prossimità.
    """
    vertices = np.asarray(other.vertices, dtype=np.float64)
    if len(vertices) == 0 or len(points) == 0:
        return None

    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(vertices)
        # Soglia allargata: la distanza al vertice più vicino sovrastima quella
        # alla superficie di un valore pari al passo della maglia.
        edge = _typical_edge_length(other)
        coarse_distance, _ = tree.query(points, k=1)
        candidates = coarse_distance <= (epsilon + edge)
    except Exception as exc:
        logger.debug("KD-tree non disponibile (%s): uso la distanza esatta", exc)
        candidates = np.ones(len(points), dtype=bool)

    if not candidates.any():
        return np.zeros(len(points), dtype=bool)

    indices = np.where(candidates)[0]
    if len(indices) > refine_limit:
        # Troppi candidati per il raffinamento esatto: la selezione grossolana
        # è già sufficientemente stretta su superfici di taglio combacianti.
        logger.debug(
            "Raffinamento della prossimità saltato: %d candidati oltre il limite",
            len(indices),
        )
        return candidates

    try:
        _, exact, _ = other.nearest.on_surface(points[indices])
    except Exception as exc:
        logger.debug("Distanza punto-superficie non calcolabile: %s", exc)
        return candidates

    mask = np.zeros(len(points), dtype=bool)
    mask[indices[exact <= epsilon]] = True
    return mask


def _typical_edge_length(mesh: trimesh.Trimesh) -> float:
    """Lunghezza mediana degli spigoli: passo caratteristico della maglia."""
    edges = mesh.edges_unique
    if len(edges) == 0:
        return 0.0
    vectors = mesh.vertices[edges[:, 0]] - mesh.vertices[edges[:, 1]]
    return float(np.median(np.linalg.norm(vectors, axis=1)))


def _bounds_touch(a: trimesh.Trimesh, b: trimesh.Trimesh, epsilon: float) -> bool:
    """Test rapido: i bounding box dei due pezzi si sfiorano?"""
    a_min, a_max = a.bounds
    b_min, b_max = b.bounds
    return bool(((a_min - epsilon) <= b_max).all() and ((b_min - epsilon) <= a_max).all())


def _robust_volume(mesh: trimesh.Trimesh) -> float:
    """Volume se la mesh è chiusa, altrimenti volume del bounding box."""
    if is_empty(mesh):
        return 0.0
    if mesh.is_watertight:
        return abs(float(mesh.volume))
    return float(np.prod(np.maximum(mesh.extents, 1e-9)))


def _perpendicular_to(direction: np.ndarray) -> np.ndarray:
    """Versore perpendicolare alla direzione data (scelta stabile)."""
    direction = np.asarray(direction, dtype=np.float64)
    reference = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(direction, reference))) > 0.9:
        reference = np.array([1.0, 0.0, 0.0])
    perpendicular = np.cross(direction, reference)
    norm = float(np.linalg.norm(perpendicular))
    if norm < 1e-9:  # pragma: no cover
        return np.array([1.0, 0.0, 0.0])
    return perpendicular / norm

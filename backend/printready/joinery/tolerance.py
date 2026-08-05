"""Calcolo delle tolleranze e dimensionamento degli incastri.

La tolleranza è la differenza fra il diametro dell'alloggiamento (femmina) e
quello della spina (maschio). Su una FDM non basta un valore unico: il gioco
utile dipende da ugello, altezza layer e orientamento della superficie, perché
l'*elephant foot* e l'allargamento del primo strato falsano le misure nominali.

Valori di riferimento pratici (PLA, ugello 0,4 mm):

===========  ===============  =========================================
Tolleranza   Tipo di accoppiamento
===========  ===============  =========================================
0,05-0,10    Forzato          richiede pressione, tenuta massima
0,10-0,20    Preciso          montaggio a mano, smontabile con sforzo
0,20-0,30    Scorrevole       montaggio e smontaggio facili (default)
0,30-0,50    Largo            per pezzi grandi o stampanti poco calibrate
===========  ===============  =========================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..domain.enums import JoineryType
from ..domain.models import JoinerySettings, PrinterProfile

logger = logging.getLogger(__name__)

#: Classi di accoppiamento con l'intervallo di gioco corrispondente, in mm.
FIT_CLASSES: dict[str, tuple[float, float, str]] = {
    "forzato": (0.05, 0.10, "Tenuta massima, montaggio a pressione"),
    "preciso": (0.10, 0.20, "Montaggio a mano, smontabile con sforzo"),
    "scorrevole": (0.20, 0.30, "Montaggio e smontaggio facili"),
    "largo": (0.30, 0.50, "Per pezzi grandi o stampanti poco calibrate"),
}


@dataclass(slots=True)
class ConnectorDimensions:
    """Dimensioni effettive di un incastro, già comprensive di tolleranza."""

    male_diameter_mm: float
    female_diameter_mm: float
    length_mm: float
    socket_depth_mm: float
    tolerance_mm: float
    taper_ratio: float = 1.0
    chamfer_mm: float = 0.0

    @property
    def radial_clearance_mm(self) -> float:
        """Gioco su un solo lato (metà della tolleranza diametrale)."""
        return (self.female_diameter_mm - self.male_diameter_mm) / 2.0

    def describe_it(self) -> str:
        return (
            f"spina Ø{self.male_diameter_mm:.2f} mm × {self.length_mm:.2f} mm, "
            f"sede Ø{self.female_diameter_mm:.2f} mm × {self.socket_depth_mm:.2f} mm "
            f"(gioco diametrale {self.tolerance_mm:.2f} mm)"
        )


def fit_class_for(tolerance_mm: float) -> tuple[str, str]:
    """Restituisce ``(nome_classe, descrizione)`` per una tolleranza data."""
    for name, (low, high, description) in FIT_CLASSES.items():
        if low <= tolerance_mm <= high:
            return name, description
    if tolerance_mm < 0.05:
        return "forzato", FIT_CLASSES["forzato"][2]
    return "largo", FIT_CLASSES["largo"][2]


def effective_tolerance(
    settings: JoinerySettings, printer: PrinterProfile, vertical_socket: bool = False
) -> float:
    """Corregge la tolleranza nominale in base al profilo di stampa.

    Args:
        settings: impostazioni di incastro scelte dall'utente.
        printer: profilo della stampante di destinazione.
        vertical_socket: ``True`` se il foro è stampato in verticale (asse Z).
            I fori verticali risultano più stretti del nominale perché il
            materiale rifluisce verso l'interno: serve un po' di gioco in più.

    Returns:
        Tolleranza effettiva in millimetri, sempre entro 0,05-0,5.
    """
    tolerance = float(settings.tolerance_mm)

    # Ugelli grandi depositano cordoli più larghi: il foro si chiude di più.
    nozzle_penalty = max(0.0, printer.nozzle_diameter_mm - 0.4) * 0.25
    tolerance += nozzle_penalty

    # Layer alti aumentano l'effetto scalino sulle pareti curve.
    layer_penalty = max(0.0, printer.layer_height_mm - 0.2) * 0.2
    tolerance += layer_penalty

    if vertical_socket:
        tolerance += 0.05

    clamped = float(min(0.5, max(0.05, round(tolerance, 3))))
    if clamped != settings.tolerance_mm:
        logger.debug(
            "Tolleranza corretta da %.3f a %.3f mm per il profilo di stampa",
            settings.tolerance_mm,
            clamped,
        )
    return clamped


def size_connector(
    settings: JoinerySettings,
    printer: PrinterProfile,
    contact_radius_mm: float,
    available_depth_mm: float,
    vertical: bool = False,
) -> ConnectorDimensions:
    """Dimensiona l'incastro adattandolo alla superficie di contatto disponibile.

    Args:
        settings: preferenze dell'utente (tipo, diametro, lunghezza, tolleranza).
        printer: profilo della stampante.
        contact_radius_mm: raggio del cerchio massimo iscrivibile nell'area di
            contatto fra i due pezzi.
        available_depth_mm: profondità utile nel pezzo femmina.
        vertical: se l'asse dell'incastro è verticale.

    Returns:
        Le dimensioni definitive di spina e sede.
    """
    tolerance = effective_tolerance(settings, printer, vertical_socket=vertical)

    diameter = float(settings.pin_diameter_mm)
    length = float(settings.pin_length_mm)

    if settings.joint_type == JoineryType.MAGNET:
        diameter = float(settings.magnet_diameter_mm)
        length = float(settings.magnet_height_mm)

    if settings.auto_scale_to_part and contact_radius_mm > 0:
        # La spina non deve superare il 55% del raggio di contatto, altrimenti
        # la parete residua attorno alla sede diventa troppo sottile.
        max_diameter = contact_radius_mm * 1.1
        min_diameter = max(2.0, printer.nozzle_diameter_mm * 5.0)
        diameter = float(min(max(min_diameter, max_diameter * 0.8), diameter * 1.5))
        diameter = min(diameter, max_diameter)

    # La sede non può essere più profonda del materiale disponibile.
    if available_depth_mm > 0:
        length = min(length, available_depth_mm * 0.8)
    length = max(length, printer.layer_height_mm * 6.0)

    # La sede è leggermente più profonda della spina: evita che il pezzo
    # resti sollevato se la spina è appena più lunga del nominale.
    socket_depth = length + max(0.3, printer.layer_height_mm * 2.0)

    return ConnectorDimensions(
        male_diameter_mm=round(diameter, 3),
        female_diameter_mm=round(diameter + tolerance, 3),
        length_mm=round(length, 3),
        socket_depth_mm=round(socket_depth, 3),
        tolerance_mm=tolerance,
        taper_ratio=float(settings.conical_taper_ratio),
        # Uno smusso all'imbocco facilita enormemente il montaggio.
        chamfer_mm=round(min(0.6, tolerance * 3.0), 3),
    )


def magnet_specification(settings: JoinerySettings) -> str:
    """Descrizione del magnete da acquistare, per la distinta materiali."""
    return (
        f"Magnete al neodimio Ø{settings.magnet_diameter_mm:.0f} × "
        f"{settings.magnet_height_mm:.0f} mm"
    )

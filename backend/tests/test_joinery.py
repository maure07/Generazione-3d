"""Test del sistema di incastri."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from printready.ai.semantic import analyze_prompt
from printready.domain.enums import JoineryType
from printready.domain.models import JoinerySettings, PrinterProfile
from printready.joinery.connectors import (
    conical_pin,
    cylindrical_pin,
    magnet_socket,
    socket_for,
    square_pin,
)
from printready.joinery.planner import JoineryPlanner
from printready.joinery.tolerance import (
    effective_tolerance,
    fit_class_for,
    size_connector,
)
from printready.segmentation.segmenter import Segmenter


class TestTolleranze:
    @pytest.mark.parametrize(
        "valore,classe",
        [(0.06, "forzato"), (0.15, "preciso"), (0.25, "scorrevole"), (0.4, "largo")],
    )
    def test_classi_di_accoppiamento(self, valore, classe):
        nome, descrizione = fit_class_for(valore)

        assert nome == classe
        assert descrizione

    def test_resta_sempre_nel_range_consentito(self):
        impostazioni = JoinerySettings(tolerance_mm=0.5)
        stampante = PrinterProfile(nozzle_diameter_mm=1.0, layer_height_mm=0.6)

        valore = effective_tolerance(impostazioni, stampante, vertical_socket=True)

        assert 0.05 <= valore <= 0.5

    def test_ugello_grande_aumenta_il_gioco(self):
        impostazioni = JoinerySettings(tolerance_mm=0.15)

        stretto = effective_tolerance(impostazioni, PrinterProfile(nozzle_diameter_mm=0.4))
        largo = effective_tolerance(impostazioni, PrinterProfile(nozzle_diameter_mm=0.8))

        assert largo > stretto

    def test_rifiuta_tolleranze_fuori_specifica(self):
        with pytest.raises(ValueError):
            JoinerySettings(tolerance_mm=0.9)

    def test_dimensionamento_rispetta_lo_spazio(self):
        dimensioni = size_connector(
            JoinerySettings(pin_diameter_mm=8.0, pin_length_mm=12.0),
            PrinterProfile(),
            contact_radius_mm=3.0,
            available_depth_mm=5.0,
        )

        # La spina non può superare lo spazio disponibile.
        assert dimensioni.male_diameter_mm <= 3.0 * 1.1
        assert dimensioni.length_mm <= 5.0 * 0.8
        assert dimensioni.female_diameter_mm > dimensioni.male_diameter_mm


class TestGeometrieIncastri:
    def test_spina_cilindrica(self):
        spina = cylindrical_pin(
            np.zeros(3), np.array([0.0, 0.0, 1.0]), diameter_mm=4.0, length_mm=6.0
        )

        assert spina.is_watertight
        assert np.isclose(spina.extents[0], 4.0, atol=0.1)

    def test_spina_conica_si_restringe(self):
        spina = conical_pin(
            np.zeros(3), np.array([0.0, 0.0, 1.0]), diameter_mm=6.0, length_mm=8.0, taper_ratio=0.6
        )

        assert spina.is_watertight
        assert spina.volume > 0

    def test_spina_quadrata(self):
        spina = square_pin(np.zeros(3), np.array([0.0, 0.0, 1.0]), size_mm=5.0, length_mm=7.0)

        assert spina.is_watertight
        assert np.isclose(spina.extents[0], 5.0, atol=0.01)

    def test_sede_magnete(self):
        sede = magnet_socket(
            np.zeros(3), np.array([0.0, 0.0, 1.0]), diameter_mm=6.0, height_mm=3.0
        )

        assert sede.is_watertight
        assert np.isclose(sede.extents[0], 6.0, atol=0.1)

    def test_la_sede_e_piu_larga_della_spina(self):
        dimensioni = size_connector(
            JoinerySettings(tolerance_mm=0.2), PrinterProfile(), 10.0, 20.0
        )
        sede = socket_for(
            JoineryType.CYLINDRICAL_PIN, np.zeros(3), np.array([0.0, 0.0, 1.0]), dimensioni
        )

        assert dimensioni.female_diameter_mm > dimensioni.male_diameter_mm
        assert dimensioni.radial_clearance_mm > 0
        assert sede.volume > 0

    def test_orientamento_lungo_una_direzione_qualsiasi(self):
        direzione = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
        spina = cylindrical_pin(np.zeros(3), direzione, diameter_mm=4.0, length_mm=10.0)

        # La spina si sviluppa nel piano XY, non lungo Z.
        assert spina.extents[2] < 5.0


class TestPianificatore:
    def _pezzi(self, figura):
        return Segmenter().segment(figura, analyze_prompt("funko con scarpe")).parts

    def test_trova_le_interfacce_fra_pezzi_adiacenti(self, figura):
        pezzi = self._pezzi(figura)

        interfacce = JoineryPlanner().find_interfaces(pezzi)

        assert len(interfacce) > 0
        assert all(i.area_mm2 > 0 for i in interfacce)
        assert all(i.radius_mm > 0 for i in interfacce)

    @pytest.mark.parametrize(
        "tipo",
        [
            JoineryType.CYLINDRICAL_PIN,
            JoineryType.CONICAL_PIN,
            JoineryType.SQUARE_PIN,
            JoineryType.MAGNET,
        ],
    )
    def test_ogni_tipo_di_incastro_produce_pezzi_validi(self, figura, tipo):
        pezzi = self._pezzi(figura)
        pianificatore = JoineryPlanner(JoinerySettings(joint_type=tipo, tolerance_mm=0.2))

        risultato = pianificatore.apply(pezzi)

        assert len(risultato.connectors) > 0
        assert all(p.mesh.is_watertight for p in pezzi)
        assert all(c.joint_type == tipo for c in risultato.connectors)

    def test_disattivato_non_modifica_nulla(self, figura):
        pezzi = self._pezzi(figura)
        volumi = [p.volume_mm3 for p in pezzi]

        risultato = JoineryPlanner(JoinerySettings(enabled=False)).apply(pezzi)

        assert risultato.connectors == []
        assert [p.volume_mm3 for p in pezzi] == volumi

    def test_un_solo_pezzo_non_richiede_incastri(self, cubo):
        from printready.segmentation.segmenter import Part

        pezzo = Part(id="a", name="Unico", part_type=None, mesh=cubo)  # type: ignore[arg-type]

        risultato = JoineryPlanner().apply([pezzo])

        assert risultato.connectors == []
        assert "nessun incastro" in risultato.message_it().lower() or risultato.notes_it

    def test_la_spina_aggiunge_al_maschio_e_scava_la_femmina(self):
        """Su due soli pezzi i ruoli sono univoci e i volumi verificabili.

        Sulla figura completa un pezzo può essere maschio verso un vicino e
        femmina verso un altro, quindi il suo volume netto non dice nulla.
        """
        from printready.domain.enums import PartType
        from printready.segmentation.segmenter import Part

        sotto = trimesh.creation.box(extents=[30, 30, 20])
        sotto.apply_translation([0, 0, 10])
        sopra = trimesh.creation.box(extents=[30, 30, 20])
        sopra.apply_translation([0, 0, 30])

        pezzi = [
            Part(id="sotto", name="Base", part_type=PartType.BASE, mesh=sotto),
            Part(id="sopra", name="Corpo", part_type=PartType.BODY, mesh=sopra),
        ]
        prima = {p.id: p.volume_mm3 for p in pezzi}

        risultato = JoineryPlanner(JoinerySettings(anti_rotation=False)).apply(pezzi)

        assert len(risultato.connectors) == 1
        connettore = risultato.connectors[0]
        # La basetta porta sempre la spina.
        assert connettore.male_part_id == "sotto"

        maschio = next(p for p in pezzi if p.id == connettore.male_part_id)
        femmina = next(p for p in pezzi if p.id == connettore.female_part_id)

        assert maschio.volume_mm3 > prima[maschio.id]
        assert femmina.volume_mm3 < prima[femmina.id]
        assert maschio.mesh.is_watertight and femmina.mesh.is_watertight

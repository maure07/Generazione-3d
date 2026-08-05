"""Test della segmentazione intelligente e dell'analisi semantica."""

from __future__ import annotations

import numpy as np
import trimesh

from printready.ai.semantic import analyze_prompt, register_terms
from printready.domain.enums import PartType
from printready.domain.models import SegmentationSettings
from printready.segmentation.anatomy import analyze_anatomy, compute_slice_profiles
from printready.segmentation.labels import infer_side
from printready.segmentation.segmenter import Segmenter, detect_arm_planes, split_by_plane


class TestAnalisiPrompt:
    def test_riconosce_le_parti_citate(self):
        analisi = analyze_prompt("personaggio con cappello, barba e spada")

        assert PartType.HAT in analisi.expected_parts
        assert PartType.BEARD in analisi.expected_parts
        assert PartType.WEAPON in analisi.expected_parts
        assert analisi.is_humanoid

    def test_riconosce_i_termini_inglesi(self):
        analisi = analyze_prompt("character with helmet and sword")

        assert PartType.HAT in analisi.expected_parts
        assert PartType.WEAPON in analisi.expected_parts

    def test_gestisce_le_negazioni(self):
        analisi = analyze_prompt("personaggio senza cappello")

        assert PartType.HAT in analisi.excluded_parts
        assert analisi.confidence_for(PartType.HAT) == 0.0

    def test_prompt_negativo(self):
        analisi = analyze_prompt("guerriero con mantello", negative_prompt="mantello")

        assert PartType.CAPE in analisi.excluded_parts

    def test_aggiunge_le_parti_implicite_di_un_umanoide(self):
        analisi = analyze_prompt("supereroe")

        assert PartType.HEAD in analisi.expected_parts
        assert PartType.BODY in analisi.expected_parts

    def test_distingue_un_oggetto(self):
        analisi = analyze_prompt("vaso decorato")

        assert not analisi.is_humanoid
        assert analisi.is_object

    def test_prompt_vuoto_assume_umanoide(self):
        analisi = analyze_prompt("")

        assert analisi.is_humanoid
        assert PartType.HEAD in analisi.expected_parts

    def test_estensione_del_lessico(self):
        register_terms(PartType.ACCESSORY, {"astrolabio"})

        analisi = analyze_prompt("mago con astrolabio")

        assert PartType.ACCESSORY in analisi.expected_parts


class TestAnatomia:
    def test_profili_delle_sezioni(self, figura):
        profili = compute_slice_profiles(figura, samples=48)

        assert len(profili) > 30
        assert all(p.area_mm2 > 0 for p in profili)
        # I profili sono ordinati dal basso verso l'alto.
        assert profili[0].z < profili[-1].z

    def test_trova_i_punti_notevoli(self, figura):
        analisi = analyze_anatomy(figura)

        assert analisi.is_humanoid
        assert analisi.neck_z is not None
        assert analisi.crotch_z is not None
        assert analisi.base_top_z is not None
        # Il collo sta sopra la biforcazione delle gambe.
        assert analisi.neck_z > analisi.crotch_z

    def test_riconosce_le_proporzioni_funko(self, figura):
        analisi = analyze_anatomy(figura)

        # La figura di prova ha una testa da 52 mm su 130 mm totali.
        assert analisi.is_funko
        assert analisi.head_ratio > 0.32


class TestTagli:
    def test_taglio_produce_due_solidi_chiusi(self, cubo):
        sopra, sotto = split_by_plane(cubo, position=0.0, axis=2)

        assert sopra.is_watertight
        assert sotto.is_watertight
        assert np.isclose(sopra.volume + sotto.volume, cubo.volume, rtol=0.01)

    def test_piano_fuori_dal_modello_non_taglia(self, cubo):
        vuoto, intero = split_by_plane(cubo, position=1000.0, axis=2)

        assert len(vuoto.faces) == 0
        assert len(intero.faces) == len(cubo.faces)

    def test_rileva_i_piani_delle_braccia(self, figura):
        analisi = analyze_anatomy(figura)
        _, sotto_collo = split_by_plane(figura, analisi.neck_z)
        torso, _ = split_by_plane(sotto_collo, analisi.crotch_z)

        piani = detect_arm_planes(torso)

        assert piani is not None
        sinistra, destra = piani
        assert sinistra < 0 < destra

    def test_nessun_falso_positivo_sulle_braccia(self):
        cilindro = trimesh.creation.cylinder(radius=20, height=100)

        assert detect_arm_planes(cilindro) is None


class TestSegmentatore:
    def test_divide_una_figura_umanoide(self, figura):
        analisi = analyze_prompt("personaggio funko con scarpe")

        risultato = Segmenter().segment(figura, analisi)

        assert risultato.count >= 5
        tipi = {p.part_type for p in risultato.parts}
        assert PartType.HEAD in tipi
        assert PartType.LEGS in tipi
        assert PartType.BASE in tipi

    def test_tutti_i_pezzi_sono_stampabili(self, figura):
        risultato = Segmenter().segment(figura, analyze_prompt("funko con scarpe"))

        assert all(p.mesh.is_watertight for p in risultato.parts)
        assert all(p.volume_mm3 > 0 for p in risultato.parts)

    def test_conserva_il_volume_complessivo(self, figura):
        risultato = Segmenter().segment(figura, analyze_prompt("funko con scarpe"))

        totale = sum(p.volume_mm3 for p in risultato.parts)
        assert np.isclose(totale, figura.volume, rtol=0.02)

    def test_separa_destra_e_sinistra(self, figura):
        risultato = Segmenter().segment(figura, analyze_prompt("funko con scarpe"))

        gambe = risultato.by_type(PartType.LEGS)
        assert len(gambe) == 2
        assert {g.side for g in gambe} == {"left", "right"}

    def test_segmentazione_disattivata(self, figura):
        impostazioni = SegmentationSettings(enabled=False)

        risultato = Segmenter(impostazioni).segment(figura, analyze_prompt("figura"))

        assert risultato.count == 1

    def test_rispetta_il_numero_massimo_di_pezzi(self, figura):
        impostazioni = SegmentationSettings(max_parts=3)

        risultato = Segmenter(impostazioni).segment(figura, analyze_prompt("funko"))

        assert risultato.count <= 3

    def test_lato_dedotto_dalla_posizione(self, cubo):
        sinistra = cubo.copy()
        sinistra.apply_translation([-50, 0, 0])
        insieme = trimesh.util.concatenate([cubo, sinistra])

        assert infer_side(sinistra, insieme) == "left"
        assert infer_side(cubo, insieme) == "right"

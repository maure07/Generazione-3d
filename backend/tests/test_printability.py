"""Test dei controlli di stampabilità e delle correzioni automatiche."""

from __future__ import annotations

import trimesh

from printready.domain.enums import IssueCode, Severity
from printready.domain.models import PrinterProfile
from printready.printability.analyzer import PrintabilityAnalyzer, aggregate_score
from printready.printability.autofix import AutoFixer, summarize_issues
from printready.printability.rules import (
    rule_inverted_normals,
    rule_non_manifold,
    rule_open_surfaces,
    rule_size,
    rule_thin_walls,
)


class TestRegole:
    def test_solido_sano_non_ha_problemi_gravi(self, cubo):
        stampante = PrinterProfile()

        assert rule_open_surfaces(cubo, stampante) == []
        assert rule_non_manifold(cubo, stampante) == []
        assert rule_inverted_normals(cubo, stampante) == []

    def test_rileva_le_pareti_sottili(self):
        lastra = trimesh.creation.box(extents=[0.4, 30, 30])

        problemi = rule_thin_walls(lastra, PrinterProfile(min_wall_mm=0.8))

        assert len(problemi) == 1
        assert problemi[0].code == IssueCode.THIN_WALL

    def test_una_parete_spessa_non_e_un_problema(self):
        blocco = trimesh.creation.box(extents=[6, 30, 30])

        assert rule_thin_walls(blocco, PrinterProfile()) == []

    def test_rileva_le_normali_invertite(self, cubo):
        invertito = cubo.copy()
        invertito.invert()

        problemi = rule_inverted_normals(invertito, PrinterProfile())

        assert any(p.code == IssueCode.INVERTED_NORMALS for p in problemi)

    def test_rileva_la_superficie_aperta(self, mesh_rotta):
        problemi = rule_open_surfaces(mesh_rotta, PrinterProfile())

        assert len(problemi) == 1
        assert problemi[0].severity == Severity.CRITICAL

    def test_rileva_il_fuori_volume(self):
        gigante = trimesh.creation.box(extents=[400, 400, 400])

        problemi = rule_size(gigante, PrinterProfile(bed_size_mm=(256, 256, 256)))

        assert len(problemi) == 1
        assert problemi[0].code == IssueCode.OVERSIZED


class TestAnalizzatore:
    def test_punteggio_alto_su_un_solido_sano(self, cubo):
        rapporto = PrintabilityAnalyzer().analyze(cubo)

        assert rapporto.score >= 90
        assert rapporto.printable
        assert "Pronto" in rapporto.verdict_it()

    def test_punteggio_basso_su_una_mesh_rotta(self, mesh_rotta):
        rapporto = PrintabilityAnalyzer().analyze(mesh_rotta)

        assert rapporto.score < 70
        assert not rapporto.printable
        assert rapporto.critical_count > 0

    def test_stime_di_tempo_e_filamento(self, sfera):
        rapporto = PrintabilityAnalyzer().analyze(sfera)

        assert rapporto.estimated_time_min > 0
        assert rapporto.estimated_filament_g > 0

    def test_mesh_vuota(self):
        from printready.mesh.io import empty_mesh

        rapporto = PrintabilityAnalyzer().analyze(empty_mesh())

        assert rapporto.score == 0
        assert not rapporto.printable

    def test_il_punteggio_complessivo_pesa_il_pezzo_peggiore(self):
        from printready.printability.analyzer import PrintabilityReport

        buono = PrintabilityReport(score=100.0)
        pessimo = PrintabilityReport(score=20.0)

        complessivo = aggregate_score([buono, buono, pessimo])

        # La media semplice darebbe 73: il minimo deve pesare di più.
        assert complessivo < 60


class TestCorrezioneAutomatica:
    def test_ripara_una_mesh_bucata(self, mesh_rotta):
        analizzatore = PrintabilityAnalyzer()
        prima = analizzatore.analyze(mesh_rotta)

        corretta, rapporto = AutoFixer().fix(mesh_rotta, prima)

        assert rapporto.score_after > rapporto.score_before
        assert corretta.is_watertight
        assert rapporto.applied_it

    def test_non_peggiora_mai_il_modello(self, sfera):
        analizzatore = PrintabilityAnalyzer()
        prima = analizzatore.analyze(sfera)

        corretta, rapporto = AutoFixer().fix(sfera, prima)

        dopo = analizzatore.analyze(corretta)
        assert dopo.score >= prima.score

    def test_dichiara_ciò_che_non_puo_correggere(self):
        # Un pezzo fuori dal volume di stampa non è correggibile in automatico.
        gigante = trimesh.creation.box(extents=[400, 400, 400])
        stampante = PrinterProfile(bed_size_mm=(256, 256, 256))

        _, rapporto = AutoFixer(stampante).fix(gigante)

        assert any("fuori volume" in n.lower() for n in rapporto.not_fixable_it)

    def test_riepilogo_dei_problemi(self, mesh_rotta):
        rapporto = PrintabilityAnalyzer().analyze(mesh_rotta)

        testo = summarize_issues(rapporto.issues)

        assert testo
        assert "Nessun problema" not in testo

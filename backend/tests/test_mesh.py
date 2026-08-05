"""Test del motore di elaborazione mesh."""

from __future__ import annotations

import numpy as np
import trimesh

from printready.mesh.components import remove_floating_shells, split_components
from printready.mesh.dedup import remove_duplicate_faces
from printready.mesh.holes import boundary_loops, close_holes
from printready.mesh.metrics import compute_stats, face_wall_thickness, overhang_faces
from printready.mesh.normals import fix_normals, has_inverted_normals
from printready.mesh.optimize import decimate, optimize_triangles, triangle_quality
from printready.mesh.repair import auto_repair, normalize_scale
from printready.mesh.solidify import extrude_surface, solidify, thicken_thin_walls
from printready.mesh.validate import detect_self_intersections, validate_stl


class TestRiparazione:
    def test_ripara_mesh_bucata(self, mesh_rotta):
        assert not mesh_rotta.is_watertight

        riparata, report = auto_repair(mesh_rotta)

        assert riparata.is_watertight
        assert report.valid
        assert report.watertight_after

    def test_non_tocca_una_mesh_sana(self, cubo):
        riparata, report = auto_repair(cubo)

        assert riparata.is_watertight
        assert report.actions_it == []
        assert np.isclose(riparata.volume, cubo.volume)

    def test_normalizza_altezza_e_appoggia_sul_piatto(self, sfera):
        scalata, fattore = normalize_scale(sfera, target_height_mm=100.0)

        assert np.isclose(scalata.extents[2], 100.0, atol=0.01)
        assert np.isclose(scalata.bounds[0][2], 0.0, atol=1e-6)
        assert fattore > 1.0


class TestBuchi:
    def test_trova_i_bordi_aperti(self, sfera):
        aperta = trimesh.Trimesh(
            vertices=sfera.vertices, faces=sfera.faces[:-20], process=False
        )
        anelli = boundary_loops(aperta)

        assert len(anelli) >= 1
        assert all(len(anello) >= 3 for anello in anelli)

    def test_chiude_i_buchi(self, sfera):
        aperta = trimesh.Trimesh(
            vertices=sfera.vertices, faces=sfera.faces[:-20], process=False
        )
        chiusa, report = close_holes(aperta)

        assert chiusa.is_watertight
        assert report.watertight
        assert report.holes_closed >= 1

    def test_mesh_gia_chiusa_resta_intatta(self, cubo):
        chiusa, report = close_holes(cubo)

        assert report.holes_before == 0
        assert len(chiusa.faces) == len(cubo.faces)


class TestNormali:
    def test_corregge_orientamento_invertito(self, cubo):
        invertito = cubo.copy()
        invertito.invert()
        assert has_inverted_normals(invertito)

        corretto, report = fix_normals(invertito)

        assert not has_inverted_normals(corretto)
        assert corretto.volume > 0

    def test_rileva_facce_degeneri(self, cubo):
        vertici = np.vstack([cubo.vertices, cubo.vertices[0]])
        facce = np.vstack([cubo.faces, [[0, 0, len(cubo.vertices)]]])
        sporco = trimesh.Trimesh(vertices=vertici, faces=facce, process=False)

        _, report = fix_normals(sporco)

        assert report.degenerate_removed >= 1


class TestDuplicati:
    def test_rimuove_le_facce_duplicate(self, cubo):
        facce = np.vstack([cubo.faces, cubo.faces[:4]])
        sporco = trimesh.Trimesh(vertices=cubo.vertices, faces=facce, process=False)

        pulito, report = remove_duplicate_faces(sporco)

        assert report.duplicate_faces == 4
        assert len(pulito.faces) == len(cubo.faces)


class TestComponenti:
    def test_divide_i_componenti_connessi(self, cubo):
        lontano = cubo.copy()
        lontano.apply_translation([100, 0, 0])
        insieme = trimesh.util.concatenate([cubo, lontano])

        assert len(split_components(insieme)) == 2

    def test_elimina_i_frammenti_piccoli(self, cubo):
        briciola = trimesh.creation.box(extents=[0.5, 0.5, 0.5])
        briciola.apply_translation([60, 0, 0])
        insieme = trimesh.util.concatenate([cubo, briciola])

        pulito, report = remove_floating_shells(insieme, min_volume_ratio=0.02)

        assert report.removed == 1
        assert len(split_components(pulito)) == 1

    def test_non_svuota_mai_il_modello(self, cubo):
        pulito, _ = remove_floating_shells(cubo, min_volume_ratio=0.99)

        assert len(pulito.faces) > 0


class TestOttimizzazione:
    def test_riduce_al_numero_di_facce_richiesto(self, sfera):
        ridotta, report = decimate(sfera, target_faces=200)

        assert len(ridotta.faces) <= 260  # tolleranza del motore QEM
        assert report.faces_before > report.faces_after
        assert report.reduction_pct > 80

    def test_non_aumenta_le_facce(self, cubo):
        ridotta, report = decimate(cubo, target_faces=10_000)

        assert len(ridotta.faces) == len(cubo.faces)
        assert report.engine == "nessuna"

    def test_conserva_la_forma_entro_una_tolleranza(self, sfera):
        ridotta, report = decimate(sfera, target_faces=400, preserve_detail=0.9)

        # Su una sfera di raggio 15 mm, mezzo millimetro è uno scarto accettabile.
        assert report.hausdorff_estimate_mm < 0.5

    def test_qualita_dei_triangoli(self, sfera):
        qualita = triangle_quality(sfera)

        assert len(qualita) == len(sfera.faces)
        assert qualita.min() > 0.5  # l'icosfera ha triangoli molto regolari

    def test_ottimizzazione_non_stravolge_il_volume(self, sfera):
        ottimizzata, _ = optimize_triangles(sfera)

        assert np.isclose(ottimizzata.volume, sfera.volume, rtol=0.05)


class TestSolidificazione:
    def test_estrude_una_superficie_aperta(self, sfera):
        calotta = trimesh.Trimesh(
            vertices=sfera.vertices,
            faces=sfera.faces[sfera.triangles_center[:, 2] > 0],
            process=False,
        )
        assert not calotta.is_watertight

        solido = extrude_surface(calotta, thickness_mm=2.0)

        assert solido.is_watertight
        assert solido.volume > 0

    def test_solidify_riconosce_un_guscio(self, sfera):
        calotta = trimesh.Trimesh(
            vertices=sfera.vertices,
            faces=sfera.faces[sfera.triangles_center[:, 2] > 0],
            process=False,
        )

        solido, report = solidify(calotta, thickness_mm=2.0, min_wall_mm=1.0)

        assert report.was_open
        assert report.extruded
        assert solido.is_watertight

    def test_ispessisce_le_pareti_sottili(self):
        guscio = trimesh.creation.box(extents=[0.6, 25, 25])

        ispessito, spostati = thicken_thin_walls(guscio, min_wall_mm=0.8)

        assert spostati > 0
        assert ispessito.extents[0] > guscio.extents[0]


class TestValidazione:
    def test_solido_valido(self, cubo):
        report = validate_stl(cubo)

        assert report.valid
        assert report.watertight
        assert report.open_edges == 0
        assert report.issues == []

    def test_rileva_superficie_aperta(self, mesh_rotta):
        report = validate_stl(mesh_rotta, check_self_intersections=False)

        assert not report.valid
        assert report.open_edges > 0
        assert any(i.code.value == "open_surface" for i in report.issues)

    def test_rileva_le_compenetrazioni(self, cubo):
        altro = cubo.copy()
        altro.apply_translation([8, 8, 8])
        insieme = trimesh.util.concatenate([cubo, altro])

        assert detect_self_intersections(insieme) > 0

    def test_nessuna_compenetrazione_su_mesh_pulita(self, sfera):
        assert detect_self_intersections(sfera) == 0


class TestMetriche:
    def test_statistiche(self, cubo):
        stats = compute_stats(cubo)

        assert stats.faces == len(cubo.faces)
        assert stats.watertight
        assert np.isclose(stats.volume_mm3, 8000.0, rtol=0.01)

    def test_spessore_di_parete(self):
        lastra = trimesh.creation.box(extents=[4.0, 30.0, 30.0])

        spessori = face_wall_thickness(lastra)
        misurati = spessori[np.isfinite(spessori)]

        assert len(misurati) > 0
        assert np.isclose(np.median(misurati), 4.0, atol=0.1)

    def test_sbalzi_su_una_sfera(self, sfera):
        maschera = overhang_faces(sfera, max_overhang_deg=45.0)

        # Metà inferiore della sfera: una parte è per forza in sbalzo.
        assert maschera.any()
        assert not maschera.all()

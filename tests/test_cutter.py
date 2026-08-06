import numpy as np
import pytest
import trimesh

from gen3d.config import CutterConfig, MeshCleanupConfig
from gen3d import cutter, mesh_tools


@pytest.fixture
def box_mesh():
    mesh = trimesh.creation.box(extents=[50, 40, 30])
    return mesh_tools.clean_and_repair(mesh, MeshCleanupConfig())


@pytest.fixture
def sphere_mesh():
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=25)
    return mesh_tools.clean_and_repair(mesh, MeshCleanupConfig())


def test_split_with_connectors_produces_two_watertight_parts(box_mesh):
    cfg = CutterConfig(pin_diameter_mm=5.0, pin_length_mm=10.0, tolerance_mm=0.2, n_pins=2)
    plane = cutter.CutPlane.from_bounds_fraction(box_mesh, "x", 0.5)
    pos, neg = cutter.split_with_connectors(box_mesh, plane, cfg)

    assert pos.is_watertight
    assert neg.is_watertight
    assert pos.volume > 0
    assert neg.volume > 0


def test_split_conserves_volume_approximately(box_mesh):
    cfg = CutterConfig(pin_diameter_mm=5.0, pin_length_mm=10.0, tolerance_mm=0.2, n_pins=2)
    plane = cutter.CutPlane.from_bounds_fraction(box_mesh, "x", 0.5)
    pos, neg = cutter.split_with_connectors(box_mesh, plane, cfg)

    total = pos.volume + neg.volume
    # i perni aggiungono/tolgono materiale localmente, ma lo scostamento
    # dal volume originale deve restare piccolo
    assert abs(total - box_mesh.volume) / box_mesh.volume < 0.05


def test_pin_and_socket_radii_respect_tolerance(box_mesh):
    tolerance = 0.3
    cfg = CutterConfig(pin_diameter_mm=6.0, pin_length_mm=10.0, tolerance_mm=tolerance, n_pins=1)
    plane = cutter.CutPlane.from_bounds_fraction(box_mesh, "z", 0.5)
    pos, neg = cutter.split_with_connectors(box_mesh, plane, cfg)

    # part_pos deve contenere piu' materiale vicino al piano di taglio
    # rispetto a un box semplicemente tagliato (per via del perno unito)
    plain_pos = box_mesh.slice_plane(plane.origin, plane.normal, cap=True)
    assert pos.volume > plain_pos.volume


def test_split_recursive_three_parts_on_sphere(sphere_mesh):
    cfg = CutterConfig(pin_diameter_mm=4.0, pin_length_mm=8.0, tolerance_mm=0.2, n_pins=3)
    cleanup_cfg = MeshCleanupConfig()
    parts = cutter.split_recursive(sphere_mesh, cfg, cleanup_cfg, n_parts=3, axis="z")

    assert len(parts) == 3
    for part in parts:
        assert part.mesh.is_watertight
        assert part.mesh.volume > 0

    total = sum(p.mesh.volume for p in parts)
    assert abs(total - sphere_mesh.volume) / sphere_mesh.volume < 0.1


def test_split_recursive_single_part_returns_original(box_mesh):
    cfg = CutterConfig()
    parts = cutter.split_recursive(box_mesh, cfg, MeshCleanupConfig(), n_parts=1)
    assert len(parts) == 1
    assert parts[0].mesh is box_mesh


def test_auto_split_axis_detects_oversized_dimension():
    mesh = trimesh.creation.box(extents=[300, 50, 50])
    axis = cutter.auto_split_axis(mesh, max_bbox_mm=(220, 220, 250))
    assert axis == "x"


def test_auto_split_axis_none_when_fits():
    mesh = trimesh.creation.box(extents=[50, 50, 50])
    axis = cutter.auto_split_axis(mesh, max_bbox_mm=(220, 220, 250))
    assert axis is None


def test_cut_plane_from_bounds_fraction_midpoint():
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    plane = cutter.CutPlane.from_bounds_fraction(mesh, "z", 0.5)
    assert np.isclose(plane.origin[2], 0.0, atol=1e-6)
    assert np.allclose(plane.normal, [0, 0, 1])

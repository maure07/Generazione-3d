import trimesh

from gen3d.config import MeshCleanupConfig
from gen3d import mesh_tools


def test_clean_and_repair_keeps_watertight_mesh_watertight():
    mesh = trimesh.creation.box(extents=[20, 20, 20])
    cfg = MeshCleanupConfig()
    repaired = mesh_tools.clean_and_repair(mesh, cfg)
    assert repaired.is_watertight
    assert repaired.volume > 0


def test_make_watertight_fills_holes():
    mesh = trimesh.creation.box(extents=[20, 20, 20])
    # rimuove una faccia per creare un buco artificiale
    faces = mesh.faces[1:]
    holed = trimesh.Trimesh(vertices=mesh.vertices, faces=faces, process=False)
    assert not holed.is_watertight

    cfg = MeshCleanupConfig()
    repaired = mesh_tools.make_watertight(holed, cfg)
    assert repaired.is_watertight


def test_remove_small_components_drops_noise_island():
    box = trimesh.creation.box(extents=[20, 20, 20])
    noise = trimesh.creation.box(extents=[0.5, 0.5, 0.5])
    noise.apply_translation([100, 100, 100])
    combined = trimesh.util.concatenate([box, noise])

    cleaned = mesh_tools.remove_small_components(combined, ratio=0.02)
    components = cleaned.split(only_watertight=False)
    assert len(components) == 1


def test_normalize_scale_sets_longest_extent():
    mesh = trimesh.creation.box(extents=[10, 5, 2])
    scaled = mesh_tools.normalize_scale(mesh, target_size_mm=100.0)
    assert abs(max(scaled.extents) - 100.0) < 1e-6
    assert (scaled.bounds[0] >= -1e-9).all()


def test_validate_printability_flags_oversized_part():
    mesh = trimesh.creation.box(extents=[300, 10, 10])
    issues = mesh_tools.validate_printability(mesh, max_bbox_mm=(220, 220, 250))
    assert any("supera il piano di stampa" in i for i in issues)


def test_validate_printability_ok_for_small_watertight_mesh():
    mesh = trimesh.creation.box(extents=[20, 20, 20])
    issues = mesh_tools.validate_printability(mesh, max_bbox_mm=(220, 220, 250))
    assert issues == []

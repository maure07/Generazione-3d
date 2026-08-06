from pathlib import Path

import trimesh

from gen3d.config import GenerationConfig
from gen3d.generation import GeneratedMesh, fuse_meshes


def _candidate(name, mesh, weight):
    return GeneratedMesh(backend=name, mesh=mesh, weight=weight, raw_path=Path("."))


def test_fuse_single_candidate_returns_same_mesh():
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    cfg = GenerationConfig()
    fused = fuse_meshes([_candidate("only", mesh, 1.0)], cfg)
    assert fused is mesh


def test_fuse_two_overlapping_meshes_low_threshold_is_union_like():
    box = trimesh.creation.box(extents=[20, 20, 20])
    sphere = trimesh.creation.icosphere(radius=12)
    sphere.apply_translation([3, 0, 0])

    cfg = GenerationConfig(voxel_pitch_mm=1.0, fusion_vote_threshold=1.0)
    fused = fuse_meshes(
        [_candidate("a", box, 1.2), _candidate("b", sphere, 0.8)], cfg
    )

    assert fused.is_watertight
    assert fused.volume > max(box.volume, sphere.volume) * 0.9


def test_fuse_two_overlapping_meshes_high_threshold_is_intersection_like():
    box = trimesh.creation.box(extents=[20, 20, 20])
    sphere = trimesh.creation.icosphere(radius=12)
    sphere.apply_translation([3, 0, 0])

    cfg = GenerationConfig(voxel_pitch_mm=1.0, fusion_vote_threshold=2.0)
    fused = fuse_meshes(
        [_candidate("a", box, 1.2), _candidate("b", sphere, 0.8)], cfg
    )

    assert fused.is_watertight
    assert fused.volume < min(box.volume, sphere.volume)

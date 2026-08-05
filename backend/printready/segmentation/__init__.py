"""Segmentazione intelligente del modello in pezzi stampabili."""

from .anatomy import AnatomyAnalysis, analyze_anatomy, compute_slice_profiles  # noqa: F401
from .labels import infer_side, label_generic_part, label_head_subpart  # noqa: F401
from .segmenter import (  # noqa: F401
    Part,
    SegmentationResult,
    Segmenter,
    color_clusters,
    dominant_color,
    merge_meshes,
    split_by_plane,
)

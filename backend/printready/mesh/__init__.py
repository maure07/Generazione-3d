"""Motore di elaborazione mesh di PrintReady AI.

Il pacchetto è organizzato per responsabilità singola: ogni modulo espone
funzioni pure che accettano e restituiscono ``trimesh.Trimesh``, così da poter
essere combinate liberamente dalla pipeline o da un plugin.
"""

from .booleans import boolean_difference, boolean_union, engine_name  # noqa: F401
from .components import remove_floating_shells, split_components  # noqa: F401
from .dedup import remove_duplicate_faces  # noqa: F401
from .holes import close_holes  # noqa: F401
from .io import load_mesh, load_scene, save_mesh  # noqa: F401
from .metrics import (  # noqa: F401
    compute_stats,
    estimate_filament_g,
    estimate_print_time_min,
    overhang_faces,
    per_vertex_curvature,
    wall_thickness,
)
from .normals import fix_normals  # noqa: F401
from .optimize import decimate, optimize_triangles  # noqa: F401
from .repair import auto_repair  # noqa: F401
from .solidify import solidify  # noqa: F401
from .validate import validate_stl  # noqa: F401

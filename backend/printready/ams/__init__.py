"""Ottimizzazione multicolore per sistemi AMS/MMU."""

from .color_extract import (  # noqa: F401
    ColorSlot,
    extract_part_colors,
    hex_to_rgb,
    name_color_it,
    quantize_colors,
    rgb_to_hex,
)
from .optimizer import AMSOptimizationResult, AMSOptimizer  # noqa: F401

"""Esportazione 3MF con materiali e colori.

Il 3MF è il formato preferibile per la stampa multicolore: è un contenitore ZIP
che descrive più oggetti, ognuno con il proprio materiale, in un solo file.
Bambu Studio, OrcaSlicer e PrusaSlicer lo importano conservando l'assegnazione
dei colori agli slot AMS.

L'esportatore è scritto direttamente sullo standard (3MF Core Specification +
Materials and Properties Extension) invece di appoggiarsi a una libreria: così
si controlla esattamente la struttura del file, comprese le estensioni
riconosciute dagli slicer.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from ..domain.enums import ExportFormat
from ..domain.models import ExportedFile
from ..mesh.io import is_empty
from .base import ExportError, Exporter, ExportItem, safe_filename

logger = logging.getLogger(__name__)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
  <Default Extension="png" ContentType="image/png"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Target="/3D/3dmodel.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

#: Identificatore della risorsa dei materiali di base.
BASE_MATERIALS_ID = 1


class ThreeMFExporter(Exporter):
    """Esportazione in 3MF con un oggetto per pezzo e un materiale per colore."""

    format = ExportFormat.THREEMF
    supports_color = True
    supports_multi_object = True

    def export(
        self, items: list[ExportItem], destination: Path, combined: bool = True
    ) -> list[ExportedFile]:
        destination = self._prepare(destination)
        valid = [i for i in items if not is_empty(i.mesh)]
        if not valid:
            raise ExportError("Nessun pezzo valido da esportare in 3MF")

        produced: list[ExportedFile] = []

        if combined:
            path = destination / "modello_completo.3mf"
            self._write(valid, path)
            file = self._describe(path, None, combined=True)
            file.slicer_hint_it = (
                "Importare in Bambu Studio o OrcaSlicer: i colori sono già "
                "assegnati agli slot AMS"
            )
            produced.append(file)

        used: set[str] = set()
        for item in valid:
            name = safe_filename(item.name)
            counter = 2
            while name in used:
                name = f"{safe_filename(item.name)}_{counter}"
                counter += 1
            used.add(name)

            path = destination / f"{name}.3mf"
            self._write([item], path)
            produced.append(self._describe(path, item.part_id, combined=False))

        return produced

    # -- scrittura del contenitore ----------------------------------------

    def _write(self, items: list[ExportItem], path: Path) -> None:
        """Costruisce il pacchetto ZIP del 3MF."""
        model_xml = self._build_model(items)
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("[Content_Types].xml", CONTENT_TYPES)
                archive.writestr("_rels/.rels", RELS)
                archive.writestr("3D/3dmodel.model", model_xml)
        except Exception as exc:
            raise ExportError(f"Scrittura del file 3MF fallita: {exc}") from exc

    def _build_model(self, items: list[ExportItem]) -> str:
        """Genera il documento XML ``3dmodel.model``."""
        materials, material_index = self._collect_materials(items)

        parts: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<model unit="millimeter" xml:lang="it-IT"'
            ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
            ' xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">',
            "  <metadata name=\"Application\">PrintReady AI</metadata>",
            "  <metadata name=\"Title\">Modello ottimizzato per la stampa</metadata>",
            "  <resources>",
        ]

        if materials:
            parts.append(f'    <basematerials id="{BASE_MATERIALS_ID}">')
            for color_hex, name_it in materials:
                parts.append(
                    f'      <base name="{escape(name_it)}" displaycolor="{_to_3mf_color(color_hex)}"/>'
                )
            parts.append("    </basematerials>")

        object_ids: list[tuple[int, str]] = []
        next_id = BASE_MATERIALS_ID + 1

        for item in items:
            mesh = item.mesh
            attributes = f'id="{next_id}" type="model" name="{escape(item.name)}"'
            if materials and item.color_hex in material_index:
                attributes += (
                    f' pid="{BASE_MATERIALS_ID}" pindex="{material_index[item.color_hex]}"'
                )
            parts.append(f"    <object {attributes}>")
            parts.append("      <mesh>")
            parts.append(_vertices_xml(mesh.vertices))
            parts.append(_triangles_xml(mesh.faces))
            parts.append("      </mesh>")
            parts.append("    </object>")
            object_ids.append((next_id, item.name))
            next_id += 1

        parts.append("  </resources>")
        parts.append("  <build>")
        for object_id, name in object_ids:
            parts.append(
                f'    <item objectid="{object_id}" '
                f'transform="1 0 0 0 1 0 0 0 1 0 0 0" partnumber="{escape(name)}"/>'
            )
        parts.append("  </build>")
        parts.append("</model>")
        return "\n".join(parts)

    def _collect_materials(
        self, items: list[ExportItem]
    ) -> tuple[list[tuple[str, str]], dict[str, int]]:
        """Raccoglie i colori distinti e il loro indice nella tavolozza."""
        from ..ams.color_extract import hex_to_rgb, name_color_it

        materials: list[tuple[str, str]] = []
        index: dict[str, int] = {}

        for item in items:
            if not item.color_hex or item.color_hex in index:
                continue
            slot_hint = (
                f"Slot {item.ams_slot + 1} - " if item.ams_slot is not None else ""
            )
            name_it = f"{slot_hint}{name_color_it(hex_to_rgb(item.color_hex))}"
            index[item.color_hex] = len(materials)
            materials.append((item.color_hex, name_it))

        return materials, index


def _to_3mf_color(color_hex: str) -> str:
    """Converte ``#rrggbb`` nel formato ``#RRGGBBAA`` richiesto dal 3MF."""
    value = (color_hex or "#cccccc").lstrip("#").upper()
    if len(value) == 6:
        return f"#{value}FF"
    if len(value) == 8:
        return f"#{value}"
    return "#CCCCCCFF"


def _vertices_xml(vertices: np.ndarray) -> str:
    """Blocco ``<vertices>`` del 3MF."""
    rows = [
        f'          <vertex x="{v[0]:.5f}" y="{v[1]:.5f}" z="{v[2]:.5f}"/>'
        for v in np.asarray(vertices, dtype=np.float64)
    ]
    return "        <vertices>\n" + "\n".join(rows) + "\n        </vertices>"


def _triangles_xml(faces: np.ndarray) -> str:
    """Blocco ``<triangles>`` del 3MF."""
    rows = [
        f'          <triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>'
        for f in np.asarray(faces, dtype=np.int64)
    ]
    return "        <triangles>\n" + "\n".join(rows) + "\n        </triangles>"

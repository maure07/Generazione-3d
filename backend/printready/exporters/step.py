"""Esportazione STEP (ISO 10303-21, AP214) come solido sfaccettato.

**Nota importante sul formato.** Lo STEP nasce per la geometria *analitica*
(piani, cilindri, spline NURBS). Un modello generato dall'AI è invece una mesh
triangolare: convertirlo in superfici analitiche richiederebbe un'operazione di
*reverse engineering* che nessun automatismo può fare senza perdere fedeltà.

Questo esportatore scrive quindi un **BREP sfaccettato**: un solido STEP valido
in cui ogni triangolo è una faccia piana. Il file si apre correttamente in
FreeCAD, Fusion 360, SolidWorks e negli slicer che accettano STEP, ma resta una
mesh: non aspettarsi facce cilindriche modificabili con i comandi CAD.

Poiché ogni triangolo genera una decina di entità, il file cresce in fretta:
oltre ``MAX_STEP_FACES`` triangoli la mesh viene decimata automaticamente e
l'utente ne viene informato nel rapporto.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import trimesh

from ..domain.enums import ExportFormat
from ..domain.models import ExportedFile
from ..mesh.io import is_empty
from .base import ExportError, Exporter, ExportItem, safe_filename

logger = logging.getLogger(__name__)

#: Oltre questa soglia il file STEP diventa ingestibile (centinaia di MB).
MAX_STEP_FACES = 40_000


class STEPExporter(Exporter):
    """Esportazione in STEP AP214 con rappresentazione a facce piane."""

    format = ExportFormat.STEP
    supports_color = False
    supports_multi_object = False

    def export(
        self, items: list[ExportItem], destination: Path, combined: bool = False
    ) -> list[ExportedFile]:
        destination = self._prepare(destination)
        produced: list[ExportedFile] = []
        used: set[str] = set()

        for item in items:
            if is_empty(item.mesh):
                continue

            mesh, decimated = _limit_faces(item.mesh)
            name = safe_filename(item.name)
            counter = 2
            while name in used:
                name = f"{safe_filename(item.name)}_{counter}"
                counter += 1
            used.add(name)

            path = destination / f"{name}.step"
            try:
                path.write_text(_build_step(mesh, item.name), encoding="ascii", errors="replace")
            except Exception as exc:
                raise ExportError(f"Esportazione STEP di «{item.name}» fallita: {exc}") from exc

            file = self._describe(path, item.part_id, combined=False)
            file.slicer_hint_it = (
                "Solido sfaccettato: apribile in CAD, ma le facce restano triangoli"
            )
            if decimated:
                file.slicer_hint_it += (
                    f" — mesh ridotta a {len(mesh.faces)} triangoli per contenere "
                    "la dimensione del file"
                )
            produced.append(file)

        return produced


def _limit_faces(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, bool]:
    """Riduce la mesh se supera il limite di facce sostenibile dal formato."""
    if len(mesh.faces) <= MAX_STEP_FACES:
        return mesh, False

    from ..mesh.optimize import decimate

    logger.info(
        "Mesh con %d facce ridotta a %d per l'esportazione STEP",
        len(mesh.faces),
        MAX_STEP_FACES,
    )
    reduced, _ = decimate(mesh, MAX_STEP_FACES, preserve_detail=0.8, adaptive=False)
    return reduced, True


def _build_step(mesh: trimesh.Trimesh, name: str) -> str:
    """Genera il testo del file STEP secondo la parte 21 dello standard.

    Struttura per ogni triangolo: tre ``CARTESIAN_POINT`` condivisi, tre
    ``VERTEX_POINT``, tre ``EDGE_CURVE`` con la relativa ``LINE``, un
    ``FACE_OUTER_BOUND`` e un ``ADVANCED_FACE`` piano. Tutte le facce
    confluiscono in un ``CLOSED_SHELL`` e in un ``MANIFOLD_SOLID_BREP``.
    """
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)

    lines: list[str] = []
    entity_id = 1

    def emit(text: str) -> int:
        """Aggiunge un'entità e restituisce il suo identificatore."""
        nonlocal entity_id
        lines.append(f"#{entity_id}={text};")
        entity_id += 1
        return entity_id - 1

    # --- contesto geometrico ---------------------------------------------
    origin = emit("CARTESIAN_POINT('',(0.,0.,0.))")
    axis_z = emit("DIRECTION('',(0.,0.,1.))")
    axis_x = emit("DIRECTION('',(1.,0.,0.))")
    placement = emit(f"AXIS2_PLACEMENT_3D('',#{origin},#{axis_z},#{axis_x})")

    uncertainty = emit(
        "UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#0,'distance_accuracy_value','')"
    )
    # Le unità sono definite dopo, quindi il riferimento #0 viene corretto sotto.
    length_unit = emit("(NAMED_UNIT(*)LENGTH_UNIT()SI_UNIT(.MILLI.,.METRE.))")
    angle_unit = emit("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    solid_unit = emit("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    lines[uncertainty - 1] = lines[uncertainty - 1].replace("#0", f"#{length_unit}")

    context = emit(
        "(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
        f"GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{uncertainty}))"
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{length_unit},#{angle_unit},#{solid_unit}))"
        "REPRESENTATION_CONTEXT('Contesto','3D'))"
    )

    # --- punti cartesiani condivisi fra le facce --------------------------
    point_ids = [
        emit(f"CARTESIAN_POINT('',({v[0]:.6f},{v[1]:.6f},{v[2]:.6f}))") for v in vertices
    ]
    vertex_ids = [emit(f"VERTEX_POINT('',#{pid})") for pid in point_ids]

    # --- una faccia piana per ogni triangolo ------------------------------
    face_ids: list[int] = []
    normals = np.asarray(mesh.face_normals, dtype=np.float64)

    for index, face in enumerate(faces):
        a, b, c = (int(x) for x in face)
        normal = normals[index]
        if not np.isfinite(normal).all() or np.linalg.norm(normal) < 1e-9:
            continue  # triangolo degenere: non produce una faccia valida

        edges: list[int] = []
        for start, end in ((a, b), (b, c), (c, a)):
            direction = vertices[end] - vertices[start]
            length = float(np.linalg.norm(direction))
            if length < 1e-12:
                break
            unit = direction / length
            dir_id = emit(f"DIRECTION('',({unit[0]:.6f},{unit[1]:.6f},{unit[2]:.6f}))")
            vector_id = emit(f"VECTOR('',#{dir_id},{length:.6f})")
            line_id = emit(f"LINE('',#{point_ids[start]},#{vector_id})")
            edge_id = emit(
                f"EDGE_CURVE('',#{vertex_ids[start]},#{vertex_ids[end]},#{line_id},.T.)"
            )
            edges.append(emit(f"ORIENTED_EDGE('',*,*,#{edge_id},.T.)"))

        if len(edges) != 3:
            continue

        loop_id = emit(f"EDGE_LOOP('',({','.join(f'#{e}' for e in edges)}))")
        bound_id = emit(f"FACE_OUTER_BOUND('',#{loop_id},.T.)")

        normal_id = emit(
            f"DIRECTION('',({normal[0]:.6f},{normal[1]:.6f},{normal[2]:.6f}))"
        )
        reference = _reference_direction(normal)
        ref_id = emit(
            f"DIRECTION('',({reference[0]:.6f},{reference[1]:.6f},{reference[2]:.6f}))"
        )
        plane_placement = emit(
            f"AXIS2_PLACEMENT_3D('',#{point_ids[a]},#{normal_id},#{ref_id})"
        )
        plane_id = emit(f"PLANE('',#{plane_placement})")
        face_ids.append(emit(f"ADVANCED_FACE('',(#{bound_id}),#{plane_id},.T.)"))

    if not face_ids:
        raise ExportError("Nessuna faccia valida da scrivere nel file STEP")

    shell_id = emit(f"CLOSED_SHELL('',({','.join(f'#{f}' for f in face_ids)}))")
    solid_id = emit(f"MANIFOLD_SOLID_BREP('{_escape(name)}',#{shell_id})")
    shape_id = emit(
        f"ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{placement},#{solid_id}),#{context})"
    )

    product_context = emit("PRODUCT_CONTEXT('',#0,'mechanical')")
    application_context = emit("APPLICATION_CONTEXT('automotive design')")
    lines[product_context - 1] = lines[product_context - 1].replace(
        "#0", f"#{application_context}"
    )
    product = emit(
        f"PRODUCT('{_escape(name)}','{_escape(name)}','',(#{product_context}))"
    )
    formation = emit(f"PRODUCT_DEFINITION_FORMATION('','',#{product})")
    definition_context = emit(
        f"PRODUCT_DEFINITION_CONTEXT('part definition',#{application_context},'design')"
    )
    definition = emit(
        f"PRODUCT_DEFINITION('','',#{formation},#{definition_context})"
    )
    shape_definition = emit(f"PRODUCT_DEFINITION_SHAPE('','',#{definition})")
    emit(f"SHAPE_DEFINITION_REPRESENTATION(#{shape_definition},#{shape_id})")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    header = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        f"FILE_DESCRIPTION(('Solido sfaccettato generato da PrintReady AI'),'2;1');\n"
        f"FILE_NAME('{_escape(name)}','{timestamp}',('PrintReady AI'),(''),"
        "'PrintReady AI 1.0','PrintReady AI','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
        "ENDSEC;\n"
        "DATA;\n"
    )
    return header + "\n".join(lines) + "\nENDSEC;\nEND-ISO-10303-21;\n"


def _reference_direction(normal: np.ndarray) -> np.ndarray:
    """Direzione di riferimento perpendicolare alla normale del piano."""
    candidate = np.array([1.0, 0.0, 0.0])
    if abs(float(np.dot(normal, candidate))) > 0.9:
        candidate = np.array([0.0, 1.0, 0.0])
    reference = np.cross(normal, candidate)
    norm = float(np.linalg.norm(reference))
    if norm < 1e-9:  # pragma: no cover
        return np.array([1.0, 0.0, 0.0])
    return reference / norm


def _escape(text: str) -> str:
    """Rende sicuro un testo dentro una stringa STEP."""
    return text.replace("'", "''").replace("\\", "\\\\")[:80]

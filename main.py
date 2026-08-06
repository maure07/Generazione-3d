#!/usr/bin/env python3
"""Punto d'ingresso UNICO della pipeline 2D -> 3D stampabile.

Un solo comando gestisce l'intero flusso (analisi immagine, generazione
3D con ensemble InstantMesh/TripoSR/LGM, pulizia/watertight, taglio con
connettori perno/sede, export multi-formato). Nessuna finestra o
dashboard separata da aprire: tutto passa da qui.

Esempi:
    python main.py check
    python main.py run --image foto.jpg --parts 2 --axis auto
    python main.py demo --shape sphere --parts 3 --pin-diameter 5 --tolerance 0.2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import trimesh

from gen3d.config import AppConfig
from gen3d.pipeline import Pipeline, setup_logging
from gen3d import mesh_tools, cutter, exporter
from gen3d.agents import LMStudioClient


def cmd_run(args: argparse.Namespace) -> int:
    cfg = AppConfig.load(args.config)
    _apply_cli_overrides(cfg, args)
    setup_logging(cfg.orchestrator.log_dir)

    pipeline = Pipeline(cfg)
    result = pipeline.run(image_path=args.image, n_parts=args.parts, axis=args.axis)

    print(json.dumps(_result_summary(result), indent=2, ensure_ascii=False))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Esegue pulizia + taglio + connettori + export su una forma primitiva.

    Utile per verificare in pochi secondi, senza GPU ne' LM Studio, che
    l'intera catena geometrica (watertight repair, boolean cut, perni/sedi,
    export STL/OBJ/3MF) funzioni sulla macchina corrente prima di lanciare
    la generazione AI, che e' molto piu' lenta.
    """
    cfg = AppConfig.load(args.config)
    _apply_cli_overrides(cfg, args)
    setup_logging(cfg.orchestrator.log_dir)

    shapes = {
        "box": lambda: trimesh.creation.box(extents=[60, 40, 30]),
        "sphere": lambda: trimesh.creation.icosphere(subdivisions=3, radius=30),
        "torus": lambda: trimesh.creation.torus(major_radius=30, minor_radius=10),
    }
    mesh = shapes[args.shape]()

    clean = mesh_tools.clean_and_repair(mesh, cfg.cleanup)
    clean = mesh_tools.normalize_scale(clean, cfg.generation.target_size_mm)

    axis = args.axis if args.axis not in (None, "auto") else "z"
    if args.parts > 1:
        parts = cutter.split_recursive(clean, cfg.cutter, cfg.cleanup, args.parts, axis)
    else:
        parts = [cutter.CutPart(mesh=clean, label="part_1")]

    parts_dict = {p.label: p.mesh for p in parts}
    for label, m in parts_dict.items():
        issues = mesh_tools.validate_printability(m, cfg.cutter.max_part_bbox_mm)
        status = "OK" if not issues else f"AVVISI: {issues}"
        print(f"{label}: watertight={m.is_watertight} volume={m.volume:.1f}mm3 facce={len(m.faces)} [{status}]")

    exported = exporter.export_all_parts(parts_dict, cfg.export)
    print(json.dumps(exported, indent=2, ensure_ascii=False))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    cfg = AppConfig.load(args.config)
    ok = True

    print("== Dipendenze Python ==")
    for module in ["trimesh", "numpy", "scipy", "shapely", "yaml", "requests"]:
        try:
            __import__(module)
            print(f"  [OK] {module}")
        except ImportError:
            print(f"  [MANCA] {module} -> pip install {module}")
            ok = False

    for optional in ["pymeshfix", "manifold3d", "skimage", "lxml"]:
        try:
            __import__(optional)
            print(f"  [OK] {optional} (opzionale)")
        except ImportError:
            print(f"  [assente] {optional} (opzionale, consigliato)")

    print("\n== LM Studio ==")
    client = LMStudioClient(cfg.lmstudio)
    if client.is_reachable():
        print(f"  [OK] server raggiungibile su {cfg.lmstudio.base_url}")
    else:
        print(f"  [MANCA] server non raggiungibile su {cfg.lmstudio.base_url} (avvia LM Studio -> Local Server)")
        ok = False

    print("\n== Checkpoint modelli locali ==")
    for label, path in [
        ("InstantMesh", cfg.models.instantmesh_ckpt),
        ("TripoSR", cfg.models.triposr_ckpt),
        ("LGM", cfg.models.lgm_ckpt),
    ]:
        exists = Path(path).exists()
        print(f"  [{'OK' if exists else 'MANCA'}] {label}: {path}")
        ok = ok and True  # i checkpoint mancanti non bloccano `demo`/`check`, solo `run`

    print("\n== Repository third_party ==")
    for label, path in [
        ("InstantMesh repo", cfg.models.instantmesh_repo),
        ("TripoSR repo", cfg.models.triposr_repo),
        ("LGM repo", cfg.models.lgm_repo),
    ]:
        exists = Path(path).exists()
        print(f"  [{'OK' if exists else 'assente'}] {label}: {path}")

    print("\n== Self-test geometrico (nessuna AI richiesta) ==")
    try:
        test_mesh = mesh_tools.clean_and_repair(trimesh.creation.box(extents=[10, 10, 10]), cfg.cleanup)
        plane = cutter.CutPlane.from_bounds_fraction(test_mesh, "z", 0.5)
        pos, neg = cutter.split_with_connectors(test_mesh, plane, cfg.cutter)
        assert pos.is_watertight and neg.is_watertight
        print("  [OK] pulizia mesh + taglio booleano + connettori perno/sede")
    except Exception as exc:
        print(f"  [FALLITO] self-test geometrico: {exc}")
        ok = False

    print(f"\nEsito: {'PRONTO' if ok else 'CONFIGURAZIONE INCOMPLETA (vedi sopra)'}")
    return 0 if ok else 1


def _apply_cli_overrides(cfg: AppConfig, args: argparse.Namespace) -> None:
    if getattr(args, "pin_diameter", None):
        cfg.cutter.pin_diameter_mm = args.pin_diameter
    if getattr(args, "tolerance", None) is not None:
        cfg.cutter.tolerance_mm = args.tolerance
    if getattr(args, "formats", None):
        cfg.export.formats = args.formats.split(",")
    if getattr(args, "output", None):
        cfg.export.output_dir = args.output
    if getattr(args, "no_vision", False):
        cfg.orchestrator.use_vision_agent = False
    if getattr(args, "no_autodebug", False):
        cfg.orchestrator.use_autodebug = False


def _result_summary(result) -> dict:
    return {
        "image": result.image_path,
        "vision_analysis": result.vision_analysis,
        "n_parts": result.n_parts,
        "split_axis": result.split_axis,
        "printability_warnings": result.printability_warnings,
        "exported_files": result.exported_files,
        "elapsed_s": round(result.elapsed_s, 1),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Pipeline locale: immagine 2D -> pezzi 3D stampabili con connettori perno/sede.",
    )
    parser.add_argument("--config", default=None, help="Percorso a config.yaml (default: ./config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--parts", type=int, default=1, help="Numero di parti in cui tagliare il modello")
    common.add_argument("--axis", default="auto", choices=["auto", "x", "y", "z"], help="Asse di taglio")
    common.add_argument("--pin-diameter", type=float, default=None, help="Diametro perno in mm")
    common.add_argument("--tolerance", type=float, default=None, help="Gioco radiale sede/perno in mm")
    common.add_argument("--formats", default=None, help="Formati di export separati da virgola, es: stl,obj,3mf")
    common.add_argument("--output", default=None, help="Cartella di output")

    p_run = sub.add_parser("run", parents=[common], help="Esegue la pipeline completa su un'immagine")
    p_run.add_argument("--image", required=True, help="Percorso dell'immagine 2D di input")
    p_run.add_argument("--no-vision", action="store_true", help="Disabilita l'analisi immagine via LM Studio")
    p_run.add_argument("--no-autodebug", action="store_true", help="Disabilita i report di autodebug")
    p_run.set_defaults(func=cmd_run)

    p_demo = sub.add_parser("demo", parents=[common], help="Self-test geometrico su una forma primitiva (no AI)")
    p_demo.add_argument("--shape", default="sphere", choices=["box", "sphere", "torus"])
    p_demo.set_defaults(func=cmd_demo)

    p_check = sub.add_parser("check", help="Verifica l'ambiente: dipendenze, LM Studio, checkpoint, repo")
    p_check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

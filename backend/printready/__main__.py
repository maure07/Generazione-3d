"""Avvio del backend da riga di comando.

Esempi::

    python -m printready                     # avvia sul default 127.0.0.1:8765
    python -m printready --port 9000         # porta personalizzata
    python -m printready --reload            # ricarica automatica (sviluppo)
"""

from __future__ import annotations

import argparse
import sys

from .config import get_settings


def main() -> int:
    """Punto d'ingresso della CLI."""
    settings = get_settings()

    parser = argparse.ArgumentParser(
        prog="printready-server",
        description="Backend di PrintReady AI",
    )
    parser.add_argument("--host", default=settings.host, help="Indirizzo di ascolto")
    parser.add_argument("--port", type=int, default=settings.port, help="Porta di ascolto")
    parser.add_argument(
        "--reload", action="store_true", help="Ricarica automatica al variare del codice"
    )
    parser.add_argument(
        "--log-level",
        default=settings.log_level.lower(),
        choices=["debug", "info", "warning", "error"],
        help="Livello di dettaglio dei messaggi",
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn non è installato. Eseguire: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    print(f"PrintReady AI {settings.version} — http://{args.host}:{args.port}")
    print(f"Documentazione API: http://{args.host}:{args.port}/docs")
    print(f"Cartella dati: {settings.data_dir}")

    uvicorn.run(
        "printready.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

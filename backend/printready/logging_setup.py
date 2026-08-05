"""Configurazione del logging: console leggibile e file rotante su disco."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from .config import get_settings

#: Formato compatto per la console, completo per il file.
CONSOLE_FORMAT = "%(levelname)-8s %(name)-32s %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)-40s %(message)s"

#: Librerie di terze parti troppo verbose al livello INFO.
NOISY_LOGGERS = ["trimesh", "httpx", "httpcore", "PIL", "shapely", "matplotlib", "urllib3"]


def setup_logging(level: str | None = None, log_dir: Path | None = None) -> None:
    """Configura il logging dell'applicazione.

    Args:
        level: livello minimo (``DEBUG``, ``INFO``, ...). Default dalle impostazioni.
        log_dir: cartella dei file di log. Default dalle impostazioni.
    """
    settings = get_settings()
    level = (level or settings.log_level).upper()
    log_dir = log_dir or settings.logs_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Rimuove eventuali handler preesistenti (riavvii in-process, test).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level, logging.INFO))
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    # 5 file da 5 MB: abbastanza per ricostruire una sessione di lavoro.
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "printready.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root.addHandler(file_handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging attivo (console: %s, file: %s)", level, log_dir / "printready.log"
    )

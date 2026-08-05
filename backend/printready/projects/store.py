"""Archivio dei progetti: persistenza, cronologia, salvataggio automatico.

Ogni progetto vive in una cartella dedicata::

    <dati>/projects/<id>/
        project.json      # stato corrente
        history/          # snapshot per undo/redo (illimitati)
        uploads/          # immagini caricate
        thumbnail.png     # anteprima per la cronologia

La scrittura è **atomica** (file temporaneo + rename): un crash a metà
salvataggio non corrompe mai il progetto.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings
from ..domain.models import (
    GenerationSettings,
    HistoryEntry,
    ImageRef,
    Project,
    ProjectSummary,
)

logger = logging.getLogger(__name__)


class ProjectNotFound(KeyError):
    """Progetto inesistente (messaggio già in italiano)."""

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Progetto non trovato: {project_id}")
        self.project_id = project_id


class ProjectStore:
    """Gestisce i progetti su disco."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().projects_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Project] = {}

    # -- percorsi ----------------------------------------------------------

    def _dir(self, project_id: str) -> Path:
        return self.root / project_id

    def _file(self, project_id: str) -> Path:
        return self._dir(project_id) / "project.json"

    def _history_dir(self, project_id: str) -> Path:
        path = self._dir(project_id) / "history"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def uploads_dir(self, project_id: str) -> Path:
        path = self._dir(project_id) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- CRUD --------------------------------------------------------------

    def create(
        self,
        name: str = "Nuovo progetto",
        prompt: str = "",
        negative_prompt: str = "",
        settings: GenerationSettings | None = None,
    ) -> Project:
        """Crea e salva un nuovo progetto."""
        project = Project(
            name=name.strip() or "Nuovo progetto",
            prompt=prompt,
            negative_prompt=negative_prompt,
        )
        if settings is not None:
            project.settings = settings
        self.save(project, snapshot_label="Creazione del progetto")
        logger.info("Progetto creato: %s (%s)", project.name, project.id[:8])
        return project

    def get(self, project_id: str) -> Project:
        """Carica un progetto (con cache in memoria)."""
        if project_id in self._cache:
            return self._cache[project_id]

        path = self._file(project_id)
        if not path.exists():
            raise ProjectNotFound(project_id)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            project = Project.model_validate(data)
        except Exception as exc:
            raise ProjectNotFound(project_id) from exc

        self._cache[project_id] = project
        return project

    def save(self, project: Project, snapshot_label: str | None = None) -> None:
        """Salva il progetto su disco in modo atomico.

        Args:
            project: progetto da salvare.
            snapshot_label: se indicato, registra anche uno snapshot nella
                cronologia undo/redo con questa etichetta.
        """
        project.updated_at = datetime.now(timezone.utc)
        directory = self._dir(project.id)
        directory.mkdir(parents=True, exist_ok=True)

        payload = project.model_dump_json(indent=2)
        target = self._file(project.id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)  # rename atomico sullo stesso filesystem

        self._cache[project.id] = project

        if snapshot_label:
            self._write_snapshot(project, snapshot_label)

    def delete(self, project_id: str) -> None:
        """Elimina il progetto e tutti i suoi file."""
        directory = self._dir(project_id)
        if not directory.exists():
            raise ProjectNotFound(project_id)
        shutil.rmtree(directory, ignore_errors=True)
        self._cache.pop(project_id, None)
        logger.info("Progetto eliminato: %s", project_id[:8])

    def list_summaries(self) -> list[ProjectSummary]:
        """Cronologia dei progetti, dal più recente."""
        summaries: list[ProjectSummary] = []
        for path in self.root.iterdir():
            if not (path / "project.json").exists():
                continue
            try:
                project = self.get(path.name)
                summaries.append(project.summary())
            except ProjectNotFound:
                continue
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    # -- immagini ----------------------------------------------------------

    def add_image(
        self, project_id: str, filename: str, content: bytes, view: str = "auto"
    ) -> ImageRef:
        """Salva un'immagine caricata e la registra nel progetto."""
        from ..exporters.base import safe_filename

        project = self.get(project_id)
        uploads = self.uploads_dir(project_id)

        stem = safe_filename(Path(filename).stem, fallback="immagine")
        suffix = Path(filename).suffix.lower() or ".png"
        stored = uploads / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
        stored.write_bytes(content)

        width, height = _image_size(stored)
        ref = ImageRef(
            filename=filename,
            path=str(stored),
            width=width,
            height=height,
            view=view if view in ("front", "back", "left", "right", "top") else "auto",
            is_primary=len(project.images) == 0,
        )
        project.images.append(ref)
        self.save(project, snapshot_label=f"Aggiunta immagine {filename}")
        return ref

    def remove_image(self, project_id: str, image_id: str) -> bool:
        """Rimuove un'immagine dal progetto (e il file dal disco)."""
        project = self.get(project_id)
        for index, image in enumerate(project.images):
            if image.id == image_id:
                Path(image.path).unlink(missing_ok=True)
                removed = project.images.pop(index)
                if removed.is_primary and project.images:
                    project.images[0].is_primary = True
                self.save(project, snapshot_label=f"Rimossa immagine {removed.filename}")
                return True
        return False

    # -- undo / redo -------------------------------------------------------

    def _write_snapshot(self, project: Project, label_it: str) -> None:
        """Scrive uno snapshot nella cronologia.

        La cronologia è una lista ordinata di file JSON; il file ``cursor``
        indica la posizione corrente. Un nuovo snapshot tronca il ramo "redo",
        come in qualunque editor.
        """
        history = self._history_dir(project.id)
        cursor = self._read_cursor(project.id)
        entries = self._snapshot_files(project.id)

        # Tronca il futuro (redo) se siamo tornati indietro.
        for stale in entries[cursor + 1 :]:
            stale.unlink(missing_ok=True)

        entry = HistoryEntry(
            project_id=project.id,
            label_it=label_it,
            snapshot=json.loads(project.model_dump_json()),
        )
        index = cursor + 1
        path = history / f"{index:06d}_{int(time.time() * 1000)}.json"
        path.write_text(entry.model_dump_json(), encoding="utf-8")
        self._write_cursor(project.id, index)

        limit = get_settings().history_limit
        if limit > 0:
            files = self._snapshot_files(project.id)
            while len(files) > limit:
                files.pop(0).unlink(missing_ok=True)

    def _snapshot_files(self, project_id: str) -> list[Path]:
        return sorted(self._history_dir(project_id).glob("*.json"))

    def _cursor_file(self, project_id: str) -> Path:
        return self._dir(project_id) / "history_cursor"

    def _read_cursor(self, project_id: str) -> int:
        try:
            return int(self._cursor_file(project_id).read_text())
        except (FileNotFoundError, ValueError):
            return len(self._snapshot_files(project_id)) - 1

    def _write_cursor(self, project_id: str, value: int) -> None:
        self._cursor_file(project_id).write_text(str(value))

    def history(self, project_id: str) -> list[dict]:
        """Elenco della cronologia per l'interfaccia (etichetta + data)."""
        entries = []
        cursor = self._read_cursor(project_id)
        for index, path in enumerate(self._snapshot_files(project_id)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append(
                    {
                        "index": index,
                        "label_it": data.get("label_it", ""),
                        "created_at": data.get("created_at", ""),
                        "current": index == cursor,
                    }
                )
            except (json.JSONDecodeError, OSError):  # pragma: no cover
                continue
        return entries

    def undo(self, project_id: str) -> Project | None:
        """Torna allo snapshot precedente. ``None`` se non c'è nulla da annullare."""
        return self._move_cursor(project_id, -1)

    def redo(self, project_id: str) -> Project | None:
        """Riapplica lo snapshot successivo. ``None`` se non c'è nulla da ripetere."""
        return self._move_cursor(project_id, +1)

    def _move_cursor(self, project_id: str, delta: int) -> Project | None:
        files = self._snapshot_files(project_id)
        if not files:
            return None
        cursor = self._read_cursor(project_id)
        target = cursor + delta
        if not (0 <= target < len(files)):
            return None

        try:
            data = json.loads(files[target].read_text(encoding="utf-8"))
            project = Project.model_validate(data["snapshot"])
        except Exception:  # pragma: no cover - snapshot corrotto
            logger.warning("Snapshot %d di %s illeggibile", target, project_id[:8])
            return None

        self._write_cursor(project_id, target)
        # Salvataggio diretto senza nuovo snapshot (siamo *dentro* la cronologia).
        project.updated_at = datetime.now(timezone.utc)
        payload = project.model_dump_json(indent=2)
        file = self._file(project_id)
        temporary = file.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(file)
        self._cache[project_id] = project
        return project


def _image_size(path: Path) -> tuple[int, int]:
    """Dimensioni di un'immagine, senza fallire su file non validi."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


#: Archivio condiviso a livello di applicazione.
store = ProjectStore()

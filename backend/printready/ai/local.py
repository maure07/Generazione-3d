"""Generatore 3D locale, senza rete e senza chiavi API.

Serve a due scopi:

1. permettere di usare PrintReady AI **offline** o in valutazione, senza
   sottoscrivere un servizio esterno;
2. fare da riserva automatica quando un provider cloud non è raggiungibile.

Algoritmo: **visual hull per space carving**.

* Ogni immagine viene ridotta a una silhouette (soggetto vs sfondo).
* Si costruisce una griglia di voxel attorno all'oggetto.
* Ogni voxel viene proiettato nelle viste disponibili: se cade fuori dalla
  silhouette in almeno una vista, viene scavato.
* Con una sola immagine si aggiunge una **stima di profondità** ricavata dalla
  distance transform della silhouette (i punti lontani dal bordo sporgono di
  più), ottenendo un solido bombato invece di una lastra piatta.
* La griglia risultante viene convertita in mesh con marching cubes e levigata.

Il risultato non compete con un modello generativo addestrato, ma è geometricamente
coerente, stagno e — soprattutto — **stampabile**: è un punto di partenza reale
per tutta la catena di ottimizzazione a valle.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import trimesh

from ..config import get_settings
from .base import (
    GenerationRequest,
    GenerationResult,
    Image3DProvider,
    ProgressCallback,
    ProviderError,
)

logger = logging.getLogger(__name__)

#: Assegnazione predefinita vista → asse di proiezione quando non specificata.
DEFAULT_VIEW_ORDER = ["front", "right", "back", "left"]


class LocalProvider(Image3DProvider):
    """Generatore locale basato su visual hull + profondità stimata."""

    name = "local"
    label_it = "Generatore locale (offline)"
    offline = True
    supports_parts = False
    supports_color = True
    max_images = 4

    def __init__(self, resolution: int = 128) -> None:
        """
        Args:
            resolution: lato della griglia di voxel. 128 è un buon compromesso
                fra dettaglio e memoria (~2 milioni di voxel).
        """
        self.resolution = int(np.clip(resolution, 32, 320))

    def is_available(self) -> bool:
        """Sempre disponibile: richiede solo numpy e Pillow."""
        try:
            import PIL  # noqa: F401

            return True
        except ImportError:  # pragma: no cover
            return False

    async def generate(
        self, request: GenerationRequest, progress: ProgressCallback | None = None
    ) -> GenerationResult:
        started = time.time()
        if not request.image_paths:
            raise ProviderError("Nessuna immagine fornita", provider=self.name, recoverable=False)

        self._report(progress, 0.05, "Estrazione delle silhouette dalle immagini")
        silhouettes = [self._silhouette(path) for path in request.image_paths[: self.max_images]]
        silhouettes = [s for s in silhouettes if s is not None]
        if not silhouettes:
            raise ProviderError(
                "Impossibile isolare il soggetto: usare un'immagine con sfondo uniforme",
                provider=self.name,
            )

        self._report(progress, 0.25, "Costruzione del volume per space carving")
        volume = self._carve(silhouettes, progress)

        self._report(progress, 0.65, "Estrazione della superficie (marching cubes)")
        mesh = self._volume_to_mesh(volume)
        if mesh is None or len(mesh.faces) == 0:
            raise ProviderError(
                "Il volume ricostruito è vuoto: provare con un'immagine più contrastata",
                provider=self.name,
            )

        self._report(progress, 0.85, "Levigatura e orientamento del modello")
        mesh = self._postprocess(mesh, silhouettes[0])

        settings = get_settings()
        out_dir = settings.cache_dir / "local_provider"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"local_{int(time.time() * 1000)}.stl"
        mesh.export(out_path)

        self._report(progress, 1.0, "Generazione locale completata")
        return GenerationResult(
            mesh_path=out_path,
            provider=self.name,
            duration_s=time.time() - started,
            raw_metadata={
                "resolution": self.resolution,
                "views": len(silhouettes),
                "faces": len(mesh.faces),
                "method": "visual_hull+depth",
            },
        )

    # -- silhouette --------------------------------------------------------

    def _silhouette(self, path: Path, size: int | None = None) -> np.ndarray | None:
        """Estrae la maschera booleana del soggetto da un'immagine.

        Strategia: se l'immagine ha canale alfa lo si usa direttamente;
        altrimenti si stima il colore di sfondo dai bordi e si marca come
        soggetto tutto ciò che se ne discosta abbastanza.
        """
        from PIL import Image

        size = size or self.resolution
        try:
            image = Image.open(path)
        except Exception as exc:
            logger.warning("Immagine non leggibile (%s): %s", path, exc)
            return None

        image = image.convert("RGBA")
        image.thumbnail((size * 2, size * 2), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        image = _fit_into(image, size)
        canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))

        data = np.asarray(canvas, dtype=np.float64)
        alpha = data[:, :, 3]
        if alpha.min() < 250:  # l'immagine ha trasparenza utile
            mask = alpha > 128
        else:
            rgb = data[:, :, :3]
            border = np.concatenate(
                [rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]], axis=0
            )
            background = np.median(border, axis=0)
            distance = np.linalg.norm(rgb - background, axis=2)
            threshold = max(28.0, float(np.percentile(distance, 55)))
            mask = distance > threshold

        mask = _fill_holes_2d(mask)
        mask = _keep_largest_blob(mask)
        if mask.sum() < 16:
            return None
        return mask

    # -- space carving -----------------------------------------------------

    def _carve(
        self, silhouettes: list[np.ndarray], progress: ProgressCallback | None
    ) -> np.ndarray:
        """Scava la griglia di voxel usando le silhouette disponibili.

        Convenzioni: X = larghezza, Y = profondità, Z = altezza.
        La vista *front* guarda lungo −Y, *right* lungo −X, e così via.
        """
        n = self.resolution
        volume = np.ones((n, n, n), dtype=bool)

        for index, mask in enumerate(silhouettes):
            view = DEFAULT_VIEW_ORDER[index % len(DEFAULT_VIEW_ORDER)]
            resized = _resize_mask(mask, n)
            # La riga 0 dell'immagine è in alto: va ribaltata sull'asse Z.
            resized = resized[::-1, :]

            if view == "front":
                # proiezione su (Z, X) per ogni Y
                keep = resized.T[:, None, :]  # (X, 1, Z)
                volume &= np.broadcast_to(keep, volume.shape)
            elif view == "back":
                keep = resized[:, ::-1].T[:, None, :]
                volume &= np.broadcast_to(keep, volume.shape)
            elif view == "right":
                # proiezione su (Z, Y) per ogni X
                keep = resized.T[None, :, :]  # (1, Y, Z)
                volume &= np.broadcast_to(keep, volume.shape)
            elif view == "left":
                keep = resized[:, ::-1].T[None, :, :]
                volume &= np.broadcast_to(keep, volume.shape)

            self._report(
                progress,
                0.25 + 0.35 * (index + 1) / max(1, len(silhouettes)),
                f"Scavo del volume dalla vista {index + 1}/{len(silhouettes)}",
            )

        if len(silhouettes) == 1:
            volume = self._apply_depth_profile(volume, _resize_mask(silhouettes[0], n)[::-1, :])

        return volume

    def _apply_depth_profile(self, volume: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Con una sola vista, limita lo spessore secondo la distance transform.

        Senza questo passo l'estrusione sarebbe un prisma a spigoli vivi; qui
        invece lo spessore cresce dove la silhouette è "spessa", producendo un
        volume bombato e simmetrico rispetto al piano centrale.
        """
        n = volume.shape[1]
        distance = _distance_transform(mask)
        if distance.max() <= 0:
            return volume

        # Profondità normalizzata: radice per un profilo più pieno del lineare.
        depth = np.sqrt(distance / distance.max())
        half_thickness = np.clip(depth * (n / 2.0) * 0.9, 1.0, n / 2.0)

        y_index = np.arange(n).reshape(1, n, 1)
        center = (n - 1) / 2.0
        # (X, Y, Z) ← half_thickness è indicizzata (Z, X): la trasponiamo.
        limit = half_thickness.T[:, None, :]
        inside = np.abs(y_index - center) <= limit
        return volume & inside

    # -- estrazione della superficie ---------------------------------------

    def _volume_to_mesh(self, volume: np.ndarray) -> trimesh.Trimesh | None:
        """Converte la griglia booleana in mesh, con marching cubes se possibile."""
        if not volume.any():
            return None

        # Padding: evita superfici aperte sui bordi della griglia.
        padded = np.pad(volume.astype(np.float32), 1, mode="constant", constant_values=0.0)

        try:
            from skimage import measure

            vertices, faces, _, _ = measure.marching_cubes(padded, level=0.5)
            # marching_cubes restituisce (i, j, k) = (X, Y, Z) nel nostro ordine.
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            logger.debug("Superficie estratta con marching cubes: %d facce", len(mesh.faces))
            return mesh
        except ImportError:
            logger.info("scikit-image assente: uso l'estrazione a voxel con levigatura")
        except Exception as exc:  # pragma: no cover
            logger.warning("Marching cubes fallito (%s): uso l'estrazione a voxel", exc)

        return _voxels_to_mesh(volume)

    def _postprocess(self, mesh: trimesh.Trimesh, mask: np.ndarray) -> trimesh.Trimesh:
        """Leviga, ripara e orienta la mesh ricostruita."""
        from ..mesh.components import largest_component
        from ..mesh.holes import close_holes
        from ..mesh.normals import fix_normals

        work = largest_component(mesh)
        work, _ = close_holes(work)
        work, _ = fix_normals(work)

        # Levigatura di Taubin: riduce la scalinatura dei voxel senza ritirare
        # il volume come farebbe un laplaciano puro.
        try:
            trimesh.smoothing.filter_taubin(work, lamb=0.5, nu=-0.53, iterations=12)
        except Exception as exc:  # pragma: no cover
            logger.debug("Levigatura Taubin non riuscita: %s", exc)

        work._cache.clear()
        # Normalizza a 100 mm di altezza: la pipeline riscalerà al valore utente.
        extents = work.extents
        if extents[2] > 1e-9:
            work.apply_scale(100.0 / float(extents[2]))
        bounds_min, bounds_max = work.bounds
        center_xy = (bounds_min[:2] + bounds_max[:2]) / 2.0
        work.apply_translation([-center_xy[0], -center_xy[1], -float(bounds_min[2])])
        return work


# ---------------------------------------------------------------------------
# Utilità di elaborazione immagini (numpy puro: nessuna dipendenza pesante)
# ---------------------------------------------------------------------------


def _fit_into(image, size: int):
    """Ridimensiona l'immagine perché entri in un quadrato ``size × size``."""
    from PIL import Image

    ratio = min(size / image.width, size / image.height)
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(new_size, Image.LANCZOS)


def _resize_mask(mask: np.ndarray, size: int) -> np.ndarray:
    """Ricampiona una maschera booleana con il nearest neighbour."""
    if mask.shape == (size, size):
        return mask
    rows = (np.linspace(0, mask.shape[0] - 1, size)).astype(int)
    cols = (np.linspace(0, mask.shape[1] - 1, size)).astype(int)
    return mask[np.ix_(rows, cols)]


def _fill_holes_2d(mask: np.ndarray) -> np.ndarray:
    """Riempie i buchi interni di una maschera con un flood fill dai bordi."""
    height, width = mask.shape
    outside = np.zeros_like(mask, dtype=bool)

    # BFS iterativa sui pixel di sfondo raggiungibili dal bordo.
    stack: list[tuple[int, int]] = []
    for x in range(width):
        for y in (0, height - 1):
            if not mask[y, x]:
                stack.append((y, x))
    for y in range(height):
        for x in (0, width - 1):
            if not mask[y, x]:
                stack.append((y, x))

    while stack:
        y, x = stack.pop()
        if y < 0 or y >= height or x < 0 or x >= width:
            continue
        if outside[y, x] or mask[y, x]:
            continue
        outside[y, x] = True
        stack.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])

    return ~outside


def _keep_largest_blob(mask: np.ndarray) -> np.ndarray:
    """Conserva solo la componente connessa più grande della maschera."""
    if not mask.any():
        return mask

    height, width = mask.shape
    labels = np.zeros(mask.shape, dtype=np.int32)
    current = 0
    best_label, best_size = 0, 0

    for start_y in range(height):
        for start_x in range(width):
            if not mask[start_y, start_x] or labels[start_y, start_x]:
                continue
            current += 1
            size = 0
            stack = [(start_y, start_x)]
            while stack:
                y, x = stack.pop()
                if y < 0 or y >= height or x < 0 or x >= width:
                    continue
                if labels[y, x] or not mask[y, x]:
                    continue
                labels[y, x] = current
                size += 1
                stack.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])
            if size > best_size:
                best_label, best_size = current, size

    return labels == best_label


def _distance_transform(mask: np.ndarray, iterations: int | None = None) -> np.ndarray:
    """Distance transform (chamfer) approssimata, in pixel.

    Implementazione a due passate: sufficiente per stimare quanto un punto è
    "interno" alla silhouette.
    """
    height, width = mask.shape
    large = float(height + width)
    distance = np.where(mask, large, 0.0)

    # Passata avanti.
    for y in range(height):
        for x in range(width):
            if not mask[y, x]:
                continue
            best = distance[y, x]
            if y > 0:
                best = min(best, distance[y - 1, x] + 1.0)
                if x > 0:
                    best = min(best, distance[y - 1, x - 1] + 1.414)
                if x < width - 1:
                    best = min(best, distance[y - 1, x + 1] + 1.414)
            if x > 0:
                best = min(best, distance[y, x - 1] + 1.0)
            distance[y, x] = best

    # Passata indietro.
    for y in range(height - 1, -1, -1):
        for x in range(width - 1, -1, -1):
            if not mask[y, x]:
                continue
            best = distance[y, x]
            if y < height - 1:
                best = min(best, distance[y + 1, x] + 1.0)
                if x > 0:
                    best = min(best, distance[y + 1, x - 1] + 1.414)
                if x < width - 1:
                    best = min(best, distance[y + 1, x + 1] + 1.414)
            if x < width - 1:
                best = min(best, distance[y, x + 1] + 1.0)
            distance[y, x] = best

    return distance


def _voxels_to_mesh(volume: np.ndarray) -> trimesh.Trimesh | None:
    """Riserva senza scikit-image: superficie dei voxel di bordo.

    Genera solo le facce esposte (non condivise con un voxel pieno vicino),
    ottenendo una mesh chiusa senza triangoli interni.
    """
    filled = np.argwhere(volume)
    if len(filled) == 0:
        return None

    occupied = set(map(tuple, filled.tolist()))
    directions = [
        ((1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
        ((-1, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
        ((0, 1, 0), [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
        ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
        ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
        ((0, 0, -1), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)]),
    ]

    vertex_index: dict[tuple[int, int, int], int] = {}
    vertices: list[tuple[int, int, int]] = []
    faces: list[tuple[int, int, int]] = []

    def index_of(point: tuple[int, int, int]) -> int:
        if point not in vertex_index:
            vertex_index[point] = len(vertices)
            vertices.append(point)
        return vertex_index[point]

    for voxel in occupied:
        for offset, corners in directions:
            neighbour = (voxel[0] + offset[0], voxel[1] + offset[1], voxel[2] + offset[2])
            if neighbour in occupied:
                continue
            quad = [index_of((voxel[0] + c[0], voxel[1] + c[1], voxel[2] + c[2])) for c in corners]
            faces.append((quad[0], quad[1], quad[2]))
            faces.append((quad[0], quad[2], quad[3]))

    if not faces:
        return None

    mesh = trimesh.Trimesh(
        vertices=np.asarray(vertices, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        process=False,
    )
    try:
        trimesh.repair.fix_winding(mesh)
    except Exception:  # pragma: no cover
        pass
    return mesh

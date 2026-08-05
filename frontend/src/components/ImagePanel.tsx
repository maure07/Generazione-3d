/**
 * Passo 1 — caricamento delle immagini.
 *
 * Accetta trascinamento, incolla dagli appunti e selezione da file. Più viste
 * dello stesso soggetto migliorano sensibilmente il risultato, quindi la cosa
 * viene detta esplicitamente invece di lasciarla intuire.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '../api/client';
import { useStore } from '../store';

export default function ImagePanel() {
  const { project, addImages, removeImage, busy } = useStore();
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = useCallback(
    (list: FileList | File[] | null) => {
      if (!list) return;
      const files = Array.from(list).filter((f) => f.type.startsWith('image/'));
      if (files.length) void addImages(files);
    },
    [addImages],
  );

  // Incollare uno screenshot è il modo più rapido di iniziare.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length) accept(files);
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [accept]);

  if (!project) return null;

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>
          <span className="step-badge">1</span> Immagini del soggetto
        </h3>
        <span className="hint">{project.images.length} caricate</span>
      </div>

      <div
        className={`dropzone ${dragging ? 'dragging' : ''}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          accept(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/bmp"
          multiple
          hidden
          onChange={(event) => accept(event.target.files)}
        />
        <span className="dropzone-icon">⬆</span>
        <p>
          Trascina qui le immagini, incollale con <kbd>Ctrl</kbd>+<kbd>V</kbd> oppure fai clic
          per sceglierle
        </p>
        <small>
          Con più viste dello stesso soggetto (fronte, lato, retro) il modello risulta molto
          più fedele.
        </small>
      </div>

      {project.images.length > 0 && (
        <ul className="thumb-grid">
          {project.images.map((image) => (
            <li key={image.id}>
              <img src={api.imageUrl(project.id, image.id)} alt={image.filename} />
              <button
                type="button"
                className="thumb-remove"
                onClick={() => void removeImage(image.id)}
                disabled={busy}
                title="Rimuovi l’immagine"
                aria-label={`Rimuovi ${image.filename}`}
              >
                ✕
              </button>
              <span className="thumb-label">
                {image.width}×{image.height}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

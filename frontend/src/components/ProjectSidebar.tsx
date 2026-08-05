/**
 * Barra laterale: cronologia dei progetti, undo/redo, elaborazione batch.
 */
import { useState } from 'react';

import { api } from '../api/client';
import { useStore } from '../store';

export default function ProjectSidebar() {
  const {
    projects,
    project,
    busy,
    newProject,
    openProject,
    removeProject,
    undo,
    redo,
    refreshProjects,
    appendLog,
  } = useStore();

  const [nome, setNome] = useState('');
  const [selezione, setSelezione] = useState<Set<string>>(new Set());

  const crea = () => {
    void newProject(nome.trim() || 'Nuovo progetto');
    setNome('');
  };

  const toggleSelezione = (id: string) => {
    setSelezione((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const avviaBatch = async () => {
    if (selezione.size === 0) return;
    try {
      const jobs = await api.generateBatch([...selezione], 2);
      appendLog(`Elaborazione batch avviata su ${jobs.length} progetti`, 'success');
      setSelezione(new Set());
      await refreshProjects();
    } catch (error) {
      appendLog(error instanceof Error ? error.message : 'Batch non avviato', 'error');
    }
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-section">
        <h3>Nuovo progetto</h3>
        <div className="inline-form">
          <input
            type="text"
            value={nome}
            placeholder="Nome del progetto"
            onChange={(event) => setNome(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') crea();
            }}
          />
          <button type="button" className="btn btn-primary" onClick={crea} disabled={busy}>
            Crea
          </button>
        </div>
      </div>

      {project && (
        <div className="sidebar-section">
          <h3>Modifiche</h3>
          <div className="button-row">
            <button type="button" className="btn btn-small" onClick={() => void undo()}>
              ↶ Annulla
            </button>
            <button type="button" className="btn btn-small" onClick={() => void redo()}>
              ↷ Ripeti
            </button>
          </div>
          <p className="hint">La cronologia è illimitata e viene salvata su disco.</p>
        </div>
      )}

      <div className="sidebar-section grow">
        <h3>
          Progetti <span className="hint">{projects.length}</span>
        </h3>

        <ul className="project-list">
          {projects.map((summary) => (
            <li
              key={summary.id}
              className={project?.id === summary.id ? 'active' : ''}
            >
              <input
                type="checkbox"
                checked={selezione.has(summary.id)}
                onChange={() => toggleSelezione(summary.id)}
                title="Seleziona per l’elaborazione batch"
                aria-label={`Seleziona ${summary.name}`}
              />
              <button
                type="button"
                className="project-open"
                onClick={() => void openProject(summary.id)}
              >
                <strong>{summary.name}</strong>
                <small>
                  {new Date(summary.updated_at).toLocaleDateString('it-IT')}
                  {summary.parts_count > 0 && ` · ${summary.parts_count} pezzi`}
                </small>
              </button>
              <button
                type="button"
                className="btn btn-tiny btn-danger"
                onClick={() => void removeProject(summary.id)}
                title="Elimina il progetto"
                aria-label={`Elimina ${summary.name}`}
              >
                ✕
              </button>
            </li>
          ))}
          {projects.length === 0 && (
            <li className="empty">Nessun progetto. Creane uno per iniziare.</li>
          )}
        </ul>
      </div>

      {selezione.size > 0 && (
        <div className="sidebar-section">
          <button type="button" className="btn btn-primary" onClick={() => void avviaBatch()}>
            Elabora {selezione.size} progetti in batch
          </button>
        </div>
      )}
    </aside>
  );
}

/**
 * Schermata principale.
 *
 * L'interfaccia segue quattro passi in orizzontale — Carica, Descrivi, Genera,
 * Esporta — perché è l'ordine mentale di chi usa l'app, non l'ordine tecnico
 * della pipeline. Le opzioni avanzate esistono ma restano nascoste finché non
 * si attiva la modalità esperto.
 */
import { useEffect } from 'react';

import ExportPanel from './components/ExportPanel';
import ImagePanel from './components/ImagePanel';
import ProgressPanel from './components/ProgressPanel';
import ProjectSidebar from './components/ProjectSidebar';
import PromptPanel from './components/PromptPanel';
import SettingsPanel from './components/SettingsPanel';
import Viewer3D from './components/Viewer3D';
import { useStore } from './store';

export default function App() {
  const { project, job, report, busy, error, uiMode, init, setUiMode, clearError, generate, cancel } =
    useStore();

  useEffect(() => {
    void init();
  }, [init]);

  const running = job?.state === 'running' || job?.state === 'pending';
  const canGenerate = Boolean(project && project.images.length > 0 && !busy);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <div>
            <h1>PrintReady AI</h1>
            <p>Da un&apos;immagine al modello stampabile</p>
          </div>
        </div>

        <div className="topbar-actions">
          <div className="mode-switch" role="group" aria-label="Modalità interfaccia">
            <button
              type="button"
              className={uiMode === 'beginner' ? 'active' : ''}
              onClick={() => setUiMode('beginner')}
            >
              Principiante
            </button>
            <button
              type="button"
              className={uiMode === 'expert' ? 'active' : ''}
              onClick={() => setUiMode('expert')}
            >
              Esperto
            </button>
          </div>
        </div>
      </header>

      {error && (
        <div className="banner banner-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={clearError} aria-label="Chiudi">
            ✕
          </button>
        </div>
      )}

      <div className="layout">
        <ProjectSidebar />

        <main className="workspace">
          {!project ? (
            <div className="empty-state">
              <h2>Nessun progetto aperto</h2>
              <p>
                Crea un nuovo progetto dal pannello di sinistra, carica un&apos;immagine del
                soggetto e scrivi una breve descrizione. Al resto pensa l&apos;applicazione.
              </p>
            </div>
          ) : (
            <>
              <ol className="steps">
                <li className={project.images.length > 0 ? 'done' : 'current'}>
                  <span className="step-number">1</span>
                  <span>Carica l&apos;immagine</span>
                </li>
                <li className={project.prompt ? 'done' : project.images.length ? 'current' : ''}>
                  <span className="step-number">2</span>
                  <span>Scrivi la descrizione</span>
                </li>
                <li className={report ? 'done' : running ? 'current' : ''}>
                  <span className="step-number">3</span>
                  <span>Genera</span>
                </li>
                <li className={report ? 'current' : ''}>
                  <span className="step-number">4</span>
                  <span>Esporta</span>
                </li>
              </ol>

              <div className="columns">
                <section className="column column-input">
                  <ImagePanel />
                  <PromptPanel />
                  {uiMode === 'expert' && <SettingsPanel />}

                  <div className="generate-row">
                    {running ? (
                      <button type="button" className="btn btn-danger" onClick={() => void cancel()}>
                        Annulla elaborazione
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-primary btn-large"
                        disabled={!canGenerate}
                        onClick={() => void generate()}
                      >
                        Genera modello 3D
                      </button>
                    )}
                  </div>
                </section>

                <section className="column column-view">
                  <Viewer3D />
                  <ProgressPanel />
                </section>

                <section className="column column-output">
                  <ExportPanel />
                </section>
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

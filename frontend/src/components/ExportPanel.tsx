/**
 * Passo 4 — risultati ed esportazione.
 *
 * Elenca i pezzi, gli incastri creati, il piano colori AMS e i file prodotti,
 * con i pulsanti per aprirli direttamente nello slicer o nell'esplora risorse.
 */
import { useEffect, useState } from 'react';

import { api } from '../api/client';
import { useStore } from '../store';

interface FileRow {
  formato: string;
  nome: string;
  percorso: string;
  dimensione_byte: number;
  part_id: string | null;
  completo: boolean;
  suggerimento_it: string | null;
}

/** Ponte verso Electron: assente quando l'app gira in un browser. */
const bridge = (
  window as unknown as {
    printready?: {
      openPath(p: string): Promise<{ ok: boolean; error?: string }>;
      showInFolder(p: string): Promise<{ ok: boolean }>;
    };
  }
).printready;

export default function ExportPanel() {
  const { job, report } = useStore();
  const [files, setFiles] = useState<FileRow[]>([]);

  useEffect(() => {
    if (job?.state !== 'completed') {
      setFiles([]);
      return;
    }
    void api
      .files(job.job_id)
      .then(setFiles)
      .catch(() => setFiles([]));
  }, [job?.job_id, job?.state]);

  if (!report || job?.state !== 'completed') {
    return (
      <div className="panel">
        <div className="panel-head">
          <h3>
            <span className="step-badge">4</span> Risultato
          </h3>
        </div>
        <p className="hint hint-block">
          Al termine della generazione qui compaiono i pezzi, gli incastri e i file pronti per
          lo slicer.
        </p>
      </div>
    );
  }

  const grouped = files.reduce<Record<string, FileRow[]>>((acc, file) => {
    (acc[file.formato] ??= []).push(file);
    return acc;
  }, {});

  const cartella = files[0]?.percorso.replace(/[\\/][^\\/]+$/, '') ?? '';

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>
          <span className="step-badge">4</span> Risultato
        </h3>
      </div>

      {/* --- Pezzi --- */}
      <section className="result-block">
        <h4>{report.parts.length} pezzi stampabili</h4>
        <ul className="result-list">
          {report.parts.map((part) => (
            <li key={part.id}>
              <span
                className="part-color"
                style={{ background: part.color_hex ?? '#7b8494' }}
                aria-hidden
              />
              <span className="grow">{part.name}</span>
              <span className="hint">
                {part.bounds
                  ? `${(part.bounds.max[0] - part.bounds.min[0]).toFixed(0)}×${(
                      part.bounds.max[1] - part.bounds.min[1]
                    ).toFixed(0)}×${(part.bounds.max[2] - part.bounds.min[2]).toFixed(0)} mm`
                  : '—'}
              </span>
            </li>
          ))}
        </ul>
      </section>

      {/* --- Incastri --- */}
      {report.connectors.length > 0 && (
        <section className="result-block">
          <h4>{report.connectors.length} incastri</h4>
          <ul className="result-list">
            {report.connectors.slice(0, 8).map((connector) => (
              <li key={connector.id}>
                <span className="grow">
                  Ø{connector.diameter_mm.toFixed(1)} × {connector.length_mm.toFixed(1)} mm
                </span>
                <span className="hint">gioco {connector.tolerance_mm.toFixed(2)} mm</span>
              </li>
            ))}
          </ul>
          {report.connectors.some((c) => c.magnet_spec) && (
            <p className="hint hint-block">
              Serve acquistare i magneti: {report.connectors.find((c) => c.magnet_spec)?.magnet_spec}
            </p>
          )}
        </section>
      )}

      {/* --- Colori AMS --- */}
      {report.ams_plan && Object.keys(report.ams_plan.slots).length > 1 && (
        <section className="result-block">
          <h4>Piano colori AMS</h4>
          <div className="slot-row">
            {Object.entries(report.ams_plan.slots).map(([slot, colore]) => (
              <div key={slot} className="slot">
                <span className="slot-swatch" style={{ background: colore }} aria-hidden />
                <span>
                  Slot {Number(slot) + 1}
                  <small>{report.ams_plan?.slot_names_it[slot]}</small>
                </span>
              </div>
            ))}
          </div>
          <p className="hint hint-block">
            {report.ams_plan.color_changes} cambi filamento (senza ottimizzazione sarebbero{' '}
            {report.ams_plan.color_changes_naive}); risparmio stimato{' '}
            {report.ams_plan.purge_waste_saved_mm3.toFixed(0)} mm³ di spurgo.
          </p>
        </section>
      )}

      {/* --- File --- */}
      <section className="result-block">
        <h4>{files.length} file esportati</h4>
        {Object.entries(grouped).map(([formato, rows]) => (
          <details key={formato} className="format-group">
            <summary>
              {formato.toUpperCase()} <span className="hint">{rows.length} file</span>
            </summary>
            <ul className="result-list">
              {rows.map((file) => (
                <li key={file.percorso}>
                  <span className="grow" title={file.percorso}>
                    {file.nome}
                  </span>
                  <span className="hint">{(file.dimensione_byte / 1024).toFixed(0)} KB</span>
                  {bridge && (
                    <button
                      type="button"
                      className="btn btn-tiny"
                      onClick={() => void bridge.showInFolder(file.percorso)}
                      title="Mostra nell’esplora risorse"
                    >
                      ↗
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </details>
        ))}

        <div className="button-row">
          {bridge && cartella && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void bridge.openPath(cartella)}
            >
              Apri la cartella dei file
            </button>
          )}
          <a
            className="btn"
            href={api.instructionsUrl(job.job_id)}
            target="_blank"
            rel="noreferrer"
          >
            Istruzioni di montaggio
          </a>
        </div>
      </section>
    </div>
  );
}

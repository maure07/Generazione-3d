/**
 * Avanzamento dell'elaborazione.
 *
 * Mostra la barra di progresso e il registro dei passi. Il registro scorre da
 * solo verso il basso: durante un'attesa di un minuto e mezzo, vedere cosa sta
 * accadendo è ciò che distingue "sta lavorando" da "si è bloccato".
 */
import { useEffect, useRef } from 'react';

import { useStore } from '../store';

export default function ProgressPanel() {
  const { job, log, report } = useStore();
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = logRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [log.length]);

  if (!job && log.length === 0) return null;

  const running = job?.state === 'running' || job?.state === 'pending';
  const percent = Math.round((job?.progress ?? 0) * 100);

  return (
    <div className="panel panel-progress">
      <div className="panel-head">
        <h3>Elaborazione</h3>
        {job && (
          <span className={`badge badge-${job.state}`}>
            {
              {
                pending: 'In coda',
                running: 'In corso',
                completed: 'Completata',
                failed: 'Non riuscita',
                cancelled: 'Annullata',
              }[job.state]
            }
          </span>
        )}
      </div>

      {running && (
        <>
          <div className="progress-track" role="progressbar" aria-valuenow={percent}>
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <p className="progress-label">
            {percent}% — {job?.message_it}
          </p>
        </>
      )}

      {report && (
        <div className="score-row">
          <div className="score-ring" data-level={scoreLevel(report.printability_score)}>
            <strong>{Math.round(report.printability_score)}</strong>
            <small>/100</small>
          </div>
          <div>
            <p className="score-verdict">{verdict(report.printability_score)}</p>
            <p className="hint">{report.summary_it}</p>
          </div>
        </div>
      )}

      {log.length > 0 && (
        <div className="log" ref={logRef}>
          {log.map((entry) => (
            <div key={entry.id} className={`log-line log-${entry.kind}`}>
              <span className="log-time">{entry.time}</span>
              <span>{entry.text}</span>
            </div>
          ))}
        </div>
      )}

      {report && report.issues.length > 0 && (
        <details className="issues">
          <summary>{report.issues.length} segnalazioni sul modello</summary>
          <ul>
            {report.issues.slice(0, 30).map((issue, index) => (
              <li key={`${issue.code}-${index}`} className={`issue issue-${issue.severity}`}>
                {issue.message_it}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function scoreLevel(score: number): string {
  if (score >= 90) return 'ok';
  if (score >= 70) return 'warn';
  return 'bad';
}

function verdict(score: number): string {
  if (score >= 90) return 'Pronto per la stampa';
  if (score >= 70) return 'Stampabile con accorgimenti';
  if (score >= 50) return 'Richiede correzioni';
  return 'Non stampabile senza intervento';
}

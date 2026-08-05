/**
 * Impostazioni avanzate (solo modalità esperto).
 *
 * Ogni controllo spiega l'effetto pratico della scelta: un numero senza
 * contesto non aiuta chi non conosce la stampa 3D, e chi la conosce apprezza
 * comunque il promemoria.
 */
import { useMemo } from 'react';

import { useStore } from '../store';
import type { ExportFormat, GenerationSettings, JoineryType } from '../api/types';

export default function SettingsPanel() {
  const { project, printers, options, updateSettings } = useStore();

  const settings = project?.settings;

  /** Descrizione dell'accoppiamento corrispondente alla tolleranza scelta. */
  const classeTolleranza = useMemo(() => {
    if (!settings || !options) return null;
    const valore = settings.joinery.tolerance_mm;
    return (
      options.classi_tolleranza.find((c) => valore >= c.minimo_mm && valore <= c.massimo_mm) ??
      null
    );
  }, [settings, options]);

  if (!project || !settings) return null;

  const patch = (changes: Partial<GenerationSettings>) => {
    void updateSettings({ ...settings, ...changes });
  };

  return (
    <div className="panel panel-settings">
      <div className="panel-head">
        <h3>Impostazioni avanzate</h3>
      </div>

      {/* --- Stampante --- */}
      <fieldset>
        <legend>Stampante</legend>

        <label className="field">
          <span>Profilo</span>
          <select
            value={settings.printer.name}
            onChange={(event) => {
              const preset = Object.values(printers).find((p) => p.name === event.target.value);
              if (preset) {
                patch({
                  printer: { ...settings.printer, ...preset } as GenerationSettings['printer'],
                });
              }
            }}
          >
            {Object.entries(printers).map(([key, preset]) => (
              <option key={key} value={preset.name}>
                {preset.name}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>
            Altezza del modello
            <em>{settings.target_height_mm.toFixed(0)} mm</em>
          </span>
          <input
            type="range"
            min={20}
            max={300}
            step={5}
            value={settings.target_height_mm}
            onChange={(event) => patch({ target_height_mm: Number(event.target.value) })}
          />
        </label>
      </fieldset>

      {/* --- Incastri --- */}
      <fieldset>
        <legend>Incastri</legend>

        <label className="field">
          <span>Tipo</span>
          <select
            value={settings.joinery.joint_type}
            onChange={(event) =>
              patch({
                joinery: {
                  ...settings.joinery,
                  joint_type: event.target.value as JoineryType,
                },
              })
            }
          >
            {options?.tipi_incastro.map((tipo) => (
              <option key={tipo.valore} value={tipo.valore}>
                {tipo.etichetta_it}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>
            Tolleranza
            <em>{settings.joinery.tolerance_mm.toFixed(2)} mm</em>
          </span>
          <input
            type="range"
            min={0.05}
            max={0.5}
            step={0.01}
            value={settings.joinery.tolerance_mm}
            onChange={(event) =>
              patch({
                joinery: { ...settings.joinery, tolerance_mm: Number(event.target.value) },
              })
            }
          />
        </label>
        {classeTolleranza && (
          <p className="hint hint-block">
            Accoppiamento <strong>{classeTolleranza.nome}</strong> —{' '}
            {classeTolleranza.descrizione_it}
          </p>
        )}

        <label className="checkbox">
          <input
            type="checkbox"
            checked={settings.joinery.anti_rotation}
            onChange={(event) =>
              patch({
                joinery: { ...settings.joinery, anti_rotation: event.target.checked },
              })
            }
          />
          Seconda spina antirotazione dove c&apos;è spazio
        </label>
      </fieldset>

      {/* --- Segmentazione --- */}
      <fieldset>
        <legend>Segmentazione</legend>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={settings.segmentation.enabled}
            onChange={(event) =>
              patch({
                segmentation: { ...settings.segmentation, enabled: event.target.checked },
              })
            }
          />
          Dividi il modello in pezzi separati
        </label>

        <label className="field">
          <span>
            Numero massimo di pezzi
            <em>{settings.segmentation.max_parts}</em>
          </span>
          <input
            type="range"
            min={1}
            max={40}
            value={settings.segmentation.max_parts}
            disabled={!settings.segmentation.enabled}
            onChange={(event) =>
              patch({
                segmentation: {
                  ...settings.segmentation,
                  max_parts: Number(event.target.value),
                },
              })
            }
          />
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={settings.segmentation.split_symmetric_pairs}
            disabled={!settings.segmentation.enabled}
            onChange={(event) =>
              patch({
                segmentation: {
                  ...settings.segmentation,
                  split_symmetric_pairs: event.target.checked,
                },
              })
            }
          />
          Separa destra e sinistra (braccia, gambe, scarpe)
        </label>
      </fieldset>

      {/* --- Qualità --- */}
      <fieldset>
        <legend>Qualità</legend>

        <label className="field">
          <span>
            Budget triangoli
            <em>{settings.optimization.target_faces.toLocaleString('it-IT')}</em>
          </span>
          <input
            type="range"
            min={5000}
            max={500000}
            step={5000}
            value={settings.optimization.target_faces}
            onChange={(event) =>
              patch({
                optimization: {
                  ...settings.optimization,
                  target_faces: Number(event.target.value),
                },
              })
            }
          />
        </label>

        <label className="field">
          <span>
            Conservazione del dettaglio
            <em>{Math.round(settings.optimization.preserve_detail * 100)}%</em>
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={settings.optimization.preserve_detail}
            onChange={(event) =>
              patch({
                optimization: {
                  ...settings.optimization,
                  preserve_detail: Number(event.target.value),
                },
              })
            }
          />
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={settings.auto_fix}
            onChange={(event) => patch({ auto_fix: event.target.checked })}
          />
          Correggi automaticamente i difetti di stampa rilevati
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={settings.solidify.hollow}
            onChange={(event) =>
              patch({ solidify: { ...settings.solidify, hollow: event.target.checked } })
            }
          />
          Svuota il modello per risparmiare filamento
        </label>
      </fieldset>

      {/* --- Esportazione --- */}
      <fieldset>
        <legend>Formati di esportazione</legend>
        <div className="chips chips-compact">
          {options?.formati_export.map((formato) => {
            const attivo = settings.export_formats.includes(formato.valore as ExportFormat);
            return (
              <button
                key={formato.valore}
                type="button"
                className={`chip ${attivo ? 'chip-active' : ''}`}
                onClick={() =>
                  patch({
                    export_formats: attivo
                      ? settings.export_formats.filter((f) => f !== formato.valore)
                      : [...settings.export_formats, formato.valore as ExportFormat],
                  })
                }
              >
                {formato.valore.toUpperCase()}
                {formato.supporta_colore && <span className="chip-dot" title="Conserva i colori" />}
              </button>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

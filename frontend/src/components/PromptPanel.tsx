/**
 * Passo 2 — descrizione testuale.
 *
 * La descrizione non serve solo a guidare la generazione: le parole
 * riconosciute (cappello, barba, spada…) determinano anche come il modello
 * verrà diviso in pezzi. Per questo mostriamo suggerimenti concreti invece di
 * un campo di testo muto.
 */
import { useEffect, useState } from 'react';

import { useStore } from '../store';

/** Esempi che coprono i casi d'uso più frequenti. */
const ESEMPI = [
  'personaggio in stile Funko Pop con cappello e barba',
  'cavaliere con armatura, mantello e spada',
  'mascotte cartoon con occhiali e zaino',
  'busto realistico con capelli lunghi',
];

export default function PromptPanel() {
  const { project, patchProject, uiMode } = useStore();
  const [prompt, setPrompt] = useState('');
  const [negative, setNegative] = useState('');

  useEffect(() => {
    setPrompt(project?.prompt ?? '');
    setNegative(project?.negative_prompt ?? '');
  }, [project?.id, project?.prompt, project?.negative_prompt]);

  if (!project) return null;

  /** Salva solo se il testo è davvero cambiato, per non riempire la cronologia. */
  const commit = () => {
    if (prompt !== project.prompt || negative !== project.negative_prompt) {
      void patchProject({ prompt, negative_prompt: negative } as never);
    }
  };

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>
          <span className="step-badge">2</span> Descrizione
        </h3>
      </div>

      <textarea
        className="prompt-input"
        rows={3}
        value={prompt}
        placeholder="Descrivi il soggetto: che cos’è e quali elementi ha"
        onChange={(event) => setPrompt(event.target.value)}
        onBlur={commit}
      />

      <div className="chips">
        {ESEMPI.map((esempio) => (
          <button
            key={esempio}
            type="button"
            className="chip"
            onClick={() => {
              setPrompt(esempio);
              void patchProject({ prompt: esempio } as never);
            }}
          >
            {esempio}
          </button>
        ))}
      </div>

      <p className="hint hint-block">
        Nominare gli elementi (<em>cappello</em>, <em>capelli</em>, <em>barba</em>,{' '}
        <em>scarpe</em>, <em>spada</em>) aiuta l&apos;applicazione a separarli in pezzi
        stampabili distinti.
      </p>

      {uiMode === 'expert' && (
        <label className="field">
          <span>Da escludere</span>
          <input
            type="text"
            value={negative}
            placeholder="es. senza basetta, senza mantello"
            onChange={(event) => setNegative(event.target.value)}
            onBlur={commit}
          />
        </label>
      )}
    </div>
  );
}

/**
 * Anteprima 3D del modello generato.
 *
 * Carica il GLB combinato prodotto dalla pipeline e lo mostra con controlli di
 * orbita. Ogni pezzo è una geometria separata nella scena, quindi è possibile
 * evidenziarlo e nasconderlo indipendentemente — utile per capire come il
 * modello è stato diviso.
 */
import { Bounds, Grid, OrbitControls, useGLTF } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { Suspense, useMemo, useState } from 'react';
import * as THREE from 'three';

import { api } from '../api/client';
import { useStore } from '../store';

/** Colore di evidenziazione del pezzo selezionato. */
const HIGHLIGHT = new THREE.Color('#5cc8ff');

interface ModelProps {
  url: string;
  hidden: Set<string>;
  selected: string | null;
}

function Model({ url, hidden, selected }: ModelProps) {
  const { scene } = useGLTF(url);

  const prepared = useMemo(() => {
    const clone = scene.clone(true);
    clone.traverse((node) => {
      if (!(node instanceof THREE.Mesh)) return;
      node.castShadow = true;
      node.receiveShadow = true;
      // Materiale proprio per ogni pezzo: così l'evidenziazione non si propaga.
      if (Array.isArray(node.material)) {
        node.material = node.material.map((m) => m.clone());
      } else if (node.material) {
        node.material = node.material.clone();
      }
    });
    return clone;
  }, [scene]);

  useMemo(() => {
    prepared.traverse((node) => {
      if (!(node instanceof THREE.Mesh)) return;
      node.visible = !hidden.has(node.name);
      const material = node.material as THREE.MeshStandardMaterial;
      if (!material) return;
      if (selected === node.name) {
        material.emissive = HIGHLIGHT;
        material.emissiveIntensity = 0.45;
      } else {
        material.emissive = new THREE.Color('#000000');
        material.emissiveIntensity = 0;
      }
    });
  }, [prepared, hidden, selected]);

  return <primitive object={prepared} />;
}

export default function Viewer3D() {
  const { job, report } = useStore();
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [exploded, setExploded] = useState(false);

  const hasPreview =
    job?.state === 'completed' && report?.exports.some((f) => f.format === 'glb');
  const url = job && hasPreview ? api.previewUrl(job.job_id) : null;

  const togglePart = (name: string) => {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <div className="viewer">
      <div className="viewer-header">
        <h3>Anteprima 3D</h3>
        {report && report.parts.length > 1 && (
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={exploded}
              onChange={(event) => setExploded(event.target.checked)}
            />
            Vista esplosa
          </label>
        )}
      </div>

      <div className="viewer-canvas">
        {url ? (
          <Canvas shadows camera={{ position: [140, 110, 160], fov: 42 }} dpr={[1, 2]}>
            <color attach="background" args={['#12151b']} />
            <ambientLight intensity={0.55} />
            <directionalLight position={[80, 140, 60]} intensity={1.6} castShadow />
            <directionalLight position={[-90, 40, -70]} intensity={0.5} />

            <Suspense fallback={null}>
              <Bounds fit clip observe margin={1.15}>
                <group scale={exploded ? 1.35 : 1}>
                  <Model url={url} hidden={hidden} selected={selected} />
                </group>
              </Bounds>
            </Suspense>

            <Grid
              args={[400, 400]}
              cellSize={10}
              cellColor="#2a3140"
              sectionSize={50}
              sectionColor="#3c4657"
              fadeDistance={520}
              infiniteGrid
              position={[0, -0.01, 0]}
            />
            <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
          </Canvas>
        ) : (
          <div className="viewer-placeholder">
            <span className="viewer-icon">◻</span>
            <p>
              {job?.state === 'running'
                ? 'Elaborazione in corso…'
                : 'L’anteprima comparirà qui al termine della generazione'}
            </p>
          </div>
        )}
      </div>

      {report && report.parts.length > 0 && (
        <ul className="part-legend">
          {report.parts.map((part) => (
            <li
              key={part.id}
              className={selected === part.name ? 'selected' : ''}
              onMouseEnter={() => setSelected(part.name)}
              onMouseLeave={() => setSelected(null)}
            >
              <button
                type="button"
                className="part-toggle"
                onClick={() => togglePart(part.name)}
                title={hidden.has(part.name) ? 'Mostra il pezzo' : 'Nascondi il pezzo'}
              >
                {hidden.has(part.name) ? '○' : '●'}
              </button>
              <span
                className="part-color"
                style={{ background: part.color_hex ?? '#7b8494' }}
                aria-hidden
              />
              <span className="part-name">{part.name}</span>
              <span className="part-meta">
                {part.faces.toLocaleString('it-IT')} tri
                {!part.watertight && <em className="warn"> · non chiuso</em>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

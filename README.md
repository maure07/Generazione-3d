# Generazione-3d

Pipeline locale (Windows-first) che converte un'immagine 2D in pezzi 3D
watertight, tagliati e uniti tramite connettori maschio/femmina (perno +
sede) con tolleranza configurabile, pronti per lo slicer (STL / OBJ / 3MF).

Tutto il flusso — analisi immagine, generazione 3D con un ensemble di tre
modelli locali, pulizia geometrica, taglio, connettori, export — parte da
**un solo comando**:

```bash
python main.py run --image foto.jpg --parts 2 --axis auto
```

Nessuna dashboard separata, nessuna finestra da cambiare: l'orchestrazione
vive tutta in questo processo Python, guidato da Claude Code.

---

## 1. Architettura della piattaforma unica

```
                     ┌──────────────────────────────────────────┐
                     │      Claude Code (hub di controllo)       │
                     │  legge/modifica il codice, lancia main.py, │
                     │  legge i log, decide i passi successivi    │
                     └───────────────────┬────────────────────────┘
                                          │  un solo comando
                                          ▼
                     ┌──────────────────────────────────────────┐
                     │                main.py (CLI)               │
                     │        run / demo / check                  │
                     └───────────────────┬────────────────────────┘
                                          ▼
                     ┌──────────────────────────────────────────┐
                     │           gen3d/pipeline.py (Pipeline)      │
                     │  orchestratore in-process, autodebug loop   │
                     └──┬───────┬────────────┬──────────┬────────┘
                        ▼       ▼            ▼          ▼
                 agents.py  generation.py mesh_tools.py cutter.py → exporter.py
                     │           │
                     ▼           ▼
              LM Studio API   subprocess verso InstantMesh / TripoSR / LGM
              (localhost:1234)  (repository clonati in third_party/)
```

Perche' e' "un'unica piattaforma di controllo":

- **Punto di ingresso unico**: `main.py` e' l'unico comando da lanciare.
  `Pipeline` (in `gen3d/pipeline.py`) e' l'unico orchestratore: chiama in
  sequenza analisi immagine, generazione, pulizia, taglio, export.
- **I sub-agenti LM Studio sono delegati, non orchestratori**: `agents.py`
  parla con LM Studio (`http://localhost:1234/v1`, API OpenAI-compatible)
  solo per due compiti puntuali (visione, debug). Il controllo del flusso,
  l'esecuzione del codice Python e il ciclo di errori restano sempre nel
  processo Python locale — LM Studio non guida mai la pipeline, la
  pipeline lo interroga quando serve.
- **I tre modelli 3D sono backend intercambiabili**: `generation.py` li
  lancia come sub-processi locali (ognuno ha il proprio ambiente
  Python/CUDA, spesso incompatibile con gli altri) e ne fonde l'output in
  un'unica mesh. Tu non apri mai i loro script direttamente: lo fa
  `main.py run`.
- **Debug in background senza uscire dalla CLI**: se una fase fallisce, la
  pipeline riprova con backoff e scrive un report di diagnosi in
  `logs/autodebug/` (via `DebugAgent`, Qwen2.5-Coder). Tu (o la sessione di
  Claude Code che guida il progetto) leggi il report e applichi la patch
  restando nello stesso ambiente: nessun altro strumento da aprire.

### Perche' Claude Code resta l'hub e LM Studio no

Claude Code (questa sessione) e' quello che legge il codice sorgente, lo
modifica quando serve, lancia `main.py`, legge stdout/log ed evolve la
pipeline. LM Studio e' solo un motore di inferenza locale dietro
un'API — esattamente come i checkpoint di InstantMesh/TripoSR/LGM sono
motori di generazione mesh dietro un `subprocess.run`. Nessuno dei due e'
un "secondo orchestratore": entrambi sono chiamati dalla stessa `Pipeline`.

---

## 2. Struttura dei file

```
Generazione-3d/
├── main.py                  # unico entry point CLI (run / demo / check)
├── config.yaml               # configurazione locale (percorsi, tolleranze)
├── requirements.txt
├── gen3d/
│   ├── config.py              # dataclass di configurazione + loader YAML
│   ├── agents.py              # client LM Studio: VisionAgent, CoderAgent, DebugAgent
│   ├── generation.py          # backend InstantMesh/TripoSR/LGM + fusione voxel
│   ├── mesh_tools.py          # pulizia, watertight repair, normali, decimazione
│   ├── cutter.py              # taglio piano + connettori perno/sede
│   ├── exporter.py            # export STL/OBJ/3MF
│   └── pipeline.py            # orchestratore + ciclo di autodebug
├── third_party/               # qui cloni InstantMesh / TripoSR / LGM (vedi sotto)
├── tests/                     # pytest, eseguibili senza GPU/LM Studio
└── output/                    # pezzi generati (creata automaticamente)
```

Ogni modulo e' testabile in isolamento: `mesh_tools.py` e `cutter.py` sono
pura geometria (trimesh/numpy/shapely) e non richiedono alcun modello AI —
per questo la test suite li verifica direttamente su primitive
(box/sfere/tori), vedi sezione 6.

---

## 3. Setup su Windows

```powershell
# 1. Ambiente Python
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2. Clona gli script di inferenza ufficiali (una tantum)
git clone https://github.com/TencentARC/InstantMesh third_party/InstantMesh
git clone https://github.com/VAST-AI-Research/TripoSR third_party/TripoSR
git clone https://github.com/3DTopia/LGM third_party/LGM

# 3. Punta config.yaml ai tuoi checkpoint gia' scaricati
#    (instant_mesh_large.ckpt, model.ckpt, model_fp16.safetensors)

# 4. Avvia LM Studio -> tab "Local Server" -> Start
#    (carica Qwen2-VL-7B, Qwen2.5-Coder-14B/7B: LM Studio gestisce il
#    caricamento/scaricamento dei modelli in memoria on-demand per nome)

# 5. Verifica che tutto sia raggiungibile
python main.py check

# 6. Genera un pezzo
python main.py run --image immagine.jpg --parts 2 --axis auto --pin-diameter 6 --tolerance 0.2
```

> I comandi di lancio di InstantMesh/TripoSR/LGM in `generation.py`
> (`DEFAULT_COMMANDS`) sono template basati sulle CLI ufficiali piu'
> comuni di questi progetti di ricerca. Le CLI dei repo di ricerca
> cambiano spesso: se il tuo clone ha flag diversi, aggiorna i comandi
> in `config.yaml` (vedi commenti in `generation.py`) invece di modificare
> il codice — e' l'unico punto dell'intera pipeline con questa dipendenza
> da "versione esterna esatta".

### Comando singolo per verificare la geometria senza AI

```bash
python main.py demo --shape sphere --parts 3 --pin-diameter 5 --tolerance 0.2
```

Esegue pulizia, taglio booleano e connettori su una forma primitiva in
pochi secondi: utile per validare l'installazione (engine booleano,
shapely, export) prima di lanciare la generazione AI, molto piu' lenta.

---

## 4. Integrazione InstantMesh + TripoSR + LGM

`generation.py` tratta i tre modelli come **backend intercambiabili**
dietro un'interfaccia comune (`SubprocessBackend.run(image, output_dir,
ckpt) -> Path`), perche' sono tre codebase PyTorch di ricerca con
dipendenze spesso incompatibili tra loro (versioni diverse di
`torch`/`xformers`/CUDA): tenerli in processi separati evita conflitti ed
e' anche piu' realistico per come questi progetti sono distribuiti
(script `run.py`/`infer.py`, non pacchetti pip).

Il valore aggiunto rispetto a usare un solo modello e' la **fusione
dell'ensemble** (`generation.fuse_meshes`):

1. Ogni mesh grezza viene normalizzata alla stessa scala e voxelizzata
   (`trimesh.voxelized(pitch).fill()`) su una griglia comune.
2. I voxel vengono sommati con un voto pesato per backend
   (`generation.fusion_weights` in config: InstantMesh pesa di piu' sui
   dettagli di superficie/spigoli, LGM sulle zone d'ombra/retro
   dell'oggetto, TripoSR fa da prior veloce e stabilizzante).
3. Un voxel e' "pieno" nella mesh finale se la somma dei pesi supera
   `fusion_vote_threshold` (soglia bassa ≈ unione, soglia alta ≈
   intersezione/consenso tra i modelli).
4. La mesh fusa e' ricostruita con marching cubes
   (`trimesh.voxel.ops.matrix_to_marching_cubes`), il che la rende
   **gia' una superficie chiusa** prima ancora del post-processing di
   `mesh_tools.py`.

Questa logica e' validata in `tests/test_generation_fusion.py` con
primitive (box + sfera sovrapposti): a soglia bassa il volume fuso si
avvicina all'unione, a soglia alta all'intersezione — il comportamento
atteso da un voto pesato multi-modello.

---

## 5. Algoritmo di taglio e connettori (perno + sede)

Cuore di `cutter.py`, funzione `split_with_connectors(mesh, plane, cfg)`:

1. **Split capped**: `mesh.slice_plane(origin, normal, cap=True)` due
   volte (normale e normale invertita) produce le due meta', **gia'
   singolarmente watertight** grazie al capping automatico di trimesh.
2. **Piazzamento perni**: la sezione trasversale sul piano di taglio viene
   estratta come poligono 2D (`mesh.section(...).to_2D()`, shapely). I
   punti di ancoraggio dei perni vengono scelti dentro il poligono eroso
   di `min_pin_edge_distance_mm` (cosi' il perno non sfonda mai un bordo
   sottile), distribuiti lungo l'asse principale della sezione (PCA) per
   evitare rotazioni relative tra le due parti una volta assemblate.
3. **Perno maschio**: cilindro allineato alla normale del piano, in parte
   annegato nella parte "positiva" (`embed_ratio`, unito con boolean
   union) e in parte sporgente oltre il piano di taglio.
4. **Sede femmina**: cilindro di raggio `pin_radius + tolerance_mm` e
   profondita' leggermente maggiore della sporgenza del perno
   (`socket_extra_depth_mm`, evita che il perno "vada in battuta" e
   impedisca la chiusura del giunto), sottratto dalla parte "negativa".
5. **Boolean engine a catena**: `manifold` (Manifold3D, robusto e
   veloce) come primo tentativo, poi `blender`, poi `scad` — se il primo
   fallisce su una mesh complessa, il secondo/terzo ci provano senza far
   fallire l'intera pipeline (vedi `_boolean` in `cutter.py`).
6. **Riparazione post-taglio**: `split_recursive` fa ripassare ogni
   pezzo da `mesh_tools.clean_and_repair` subito dopo il taglio, cosi' un
   'artefatto introdotto dal boolean non si propaga ai tagli successivi
   in caso di split multipli (`n_parts > 2`).

Validato in `tests/test_cutter.py`: watertightness di entrambe le meta',
conservazione approssimativa del volume, rispetto del raggio della sede
rispetto alla tolleranza richiesta, split ricorsivo a 3 parti su una sfera.

---

## 6. Test eseguibili senza GPU/LM Studio

```bash
python -m pytest tests/ -v
```

17 test coprono `mesh_tools.py`, `cutter.py` e la fusione dell'ensemble in
`generation.py`, tutti su primitive trimesh (box, sfere, tori): non serve
alcun checkpoint AI ne' LM Studio per validare che la geometria sia
corretta. E' il modo consigliato per verificare l'installazione dopo aver
clonato il repo o modificato `cutter.py`/`mesh_tools.py`.

---

## 7. Valutazione tecnica e miglioramenti consigliati

Feedback onesto, non solo elenco di feature:

**Cosa migliorerei per una geometria piu' precisa:**

- **Fusione dell'ensemble**: il voto voxel-binario implementato qui e'
  robusto ma "grezzo" — perde informazione sub-voxel. Un miglioramento
  concreto e' passare a una **fusione via SDF/TSDF** (ricostruire ogni
  mesh come signed distance field con `mesh_to_sdf` o l'integrazione TSDF
  di Open3D, poi fondere le SDF con media pesata e ricostruire con
  marching cubes sul campo continuo): preserva dettagli fini e produce
  transizioni piu' lisce tra i contributi dei tre modelli rispetto al voto
  binario per-voxel.
- **Allineamento tra i tre output**: oggi normalizzo scala e centro il
  centroide di ogni mesh, ma non c'e' un vero **allineamento rigido**
  (ICP) tra InstantMesh/TripoSR/LGM prima della fusione. Se i tre modelli
  producono orientamenti leggermente diversi, la fusione voxel puo'
  "sfocare" i dettagli. Aggiungerei un passo di ICP (Open3D
  `registration_icp`) usando la mesh InstantMesh come riferimento.
- **Retopology/quad-dominant mesh** per gli spigoli: FlexiCubes
  (InstantMesh) da' gia' buoni risultati su geometria rigida, ma per
  spigoli davvero netti su parti meccaniche vale la pena aggiungere un
  passo di **feature-preserving remeshing** (es. Instant Meshes di
  Jakob et al., o `pymeshlab` con `meshing_isotropic_explicit_remeshing`
  + rilevamento spigoli) dopo la fusione e prima del taglio.
- **Perni non circolari** per pezzi grandi: un perno cilindrico singolo
  non blocca la rotazione; con `n_pins=1` su superfici piccole conviene
  passare a una **chiave a D o esagonale** (basta sostituire
  `trimesh.creation.cylinder` con un profilo estruso) per garantire
  l'incastro anche a un solo punto di ancoraggio.

**Strumenti/librerie da integrare per non far mai fallire i booleani su
mesh complesse:**

- **Manifold3D** (gia' integrato come motore primario in `cutter.py`):
  e' oggi lo stato dell'arte per booleani robusti su mesh non
  perfettamente manifold — lo consiglio come prima linea di difesa, gia'
  fatto qui.
- **Blender headless** (`blender --background --python script.py`) come
  secondo fallback: il modificatore Boolean di Blender (Exact/BMesh) e'
  storicamente il piu' tollerante con geometrie sporche (facce
  auto-intersecanti, T-junction), a costo di essere piu' lento e di
  richiedere Blender installato e raggiungibile da CLI. E' gia' previsto
  come secondo anello della catena `boolean_engines` in `config.yaml`: va
  solo installato e aggiunto al PATH.
- **OpenSCAD** come terzo fallback via CGAL (`trimesh` engine `"scad"`):
  piu' lento di Manifold3D ma matematicamente esatto, utile come ultima
  rete di sicurezza su mesh patologiche.
- **PyMeshLab**: utilissimo per un passo di *mesh repair* piu' aggressivo
  di `pymeshfix` prima del taglio (es. `meshing_repair_non_manifold_edges`,
  `meshing_close_holes` con controllo sull'area massima del buco) quando
  la mesh fusa dall'ensemble ha topologia particolarmente sporca.
- **Open3D** (gia' citato per ICP/TSDF sopra): utile anche per
  `simplify_quadric_decimation` piu' stabile della funzione equivalente
  di trimesh su mesh molto dense, e per una validazione watertight
  indipendente (`open3d.geometry.TriangleMesh.is_watertight()`) da usare
  come doppio controllo prima dell'export.

In sintesi: l'architettura a catena di fallback per i booleani (Manifold3D
→ Blender → OpenSCAD) e' gia' quella giusta per "non fallire mai"; il
prossimo salto di qualita' reale sta nel passare da fusione voxel binaria
a fusione SDF continua con allineamento ICP tra i tre modelli.

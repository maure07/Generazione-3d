# PrintReady AI

**Da un'immagine e una descrizione a un modello 3D diviso in pezzi, con incastri,
colori AMS e file pronti per lo slicer.**

Applicazione desktop per Windows che trasforma una fotografia o un disegno in un
modello stampabile in FDM, senza richiedere alcuna competenza di modellazione.

```
Carica immagine  →  Scrivi descrizione  →  Genera  →  Esporta
```

---

## Che cosa fa davvero

Molti strumenti generano una mesh da un'immagine. Il problema è che quella mesh
**non è stampabile**: ha buchi, pareti da un decimo di millimetro, normali
invertite, ed è un blocco unico che nessuna stampante FDM riesce a produrre
senza una montagna di supporti.

PrintReady AI prende quella mesh e la porta fino al file da mandare in stampa:

| Fase | Che cosa succede |
|------|------------------|
| **Riparazione** | Chiude i buchi, corregge le normali, elimina facce duplicate e gusci vaganti |
| **Solidificazione** | Trasforma le superfici aperte in solidi, ispessisce le pareti sotto il minimo estrudibile |
| **Ottimizzazione** | Riduce i poligoni conservando il dettaglio dove la curvatura è alta (volti, mani) |
| **Segmentazione** | Divide la figura in pezzi come un Funko Pop: testa, capelli, cappello, corpo, braccia, gambe, scarpe, basetta |
| **Incastri** | Crea spine e alloggiamenti con tolleranze da 0,05 a 0,5 mm, così i pezzi si montano a incastro |
| **Controllo stampa** | Verifica pareti sottili, isole, sbalzi, non-manifold, compenetrazioni — e corregge quel che è correggibile |
| **Colori AMS** | Raggruppa i pezzi per colore riducendo i cambi filamento e lo spreco della torre di spurgo |
| **Esportazione** | STL, OBJ, 3MF (con i colori), STEP, GLB, più le istruzioni di montaggio |

Il principio che governa tutte le correzioni automatiche: **mai peggiorare**.
Ogni modifica viene misurata e, se abbassa il punteggio di stampabilità, viene
annullata e segnalata invece di essere applicata in silenzio.

---

## Installazione

### Requisiti

- Windows 10/11 a 64 bit (funziona anche su Linux e macOS)
- Python 3.10 o successivo
- Node.js 18 o successivo (solo per compilare l'interfaccia)
- GPU NVIDIA con CUDA — facoltativa, accelera i modelli locali

### Avvio rapido

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate          # su Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# 2. Interfaccia
cd ../frontend
npm install

# 3. Avvio in sviluppo (backend + finestra desktop)
npm run dev
```

Per produrre l'installatore Windows:

```bash
cd frontend
npm run dist        # genera release/PrintReadyAI-1.0.0-win64.exe
```

Il backend può anche girare da solo, per usarlo via API:

```bash
cd backend
python -m printready              # http://127.0.0.1:8765
                                  # documentazione su /docs
```

---

## Configurazione dei generatori 3D

L'applicazione funziona **subito, senza chiavi API**: il generatore locale
ricostruisce il volume dalle silhouette con la tecnica dello *space carving*.
Non compete con un modello generativo addestrato, ma produce geometria coerente
e stampabile, ed è la rete di sicurezza quando il cloud non risponde.

Per risultati migliori si configura un provider esterno creando un file
`backend/.env`:

```ini
PRINTREADY_TRIPO_API_KEY=...        # Tripo AI
PRINTREADY_MESHY_API_KEY=...        # Meshy AI
PRINTREADY_HUNYUAN_ENDPOINT=http://127.0.0.1:8080   # Hunyuan3D self-hosted
```

Con `ai_provider = "auto"` i provider vengono provati in ordine di qualità
attesa, con ricaduta automatica sul generatore locale.

---

## Struttura del progetto

```
backend/printready/
├── domain/          modelli, enumerazioni, bus eventi
├── ai/              provider 3D (Tripo, Meshy, Hunyuan3D, locale) e analisi del prompt
├── mesh/            riparazione, solidificazione, ottimizzazione, validazione
├── segmentation/    analisi anatomica e divisione in pezzi
├── joinery/         spine, sedi, magneti, tolleranze
├── printability/    regole di stampa e correzioni automatiche
├── ams/             ottimizzazione multicolore
├── exporters/       STL, OBJ, 3MF, STEP, GLB
├── pipeline/        orchestrazione del workflow e gestione dei job
├── projects/        persistenza, cronologia, undo/redo, autosalvataggio
├── plugins/         sistema di estensioni
└── api/             API REST e WebSocket

frontend/
├── electron/        processo principale e ponte sicuro verso il renderer
└── src/             interfaccia React con anteprima 3D (three.js)
```

Documentazione approfondita in [`docs/`](docs/):

- [Architettura](docs/ARCHITETTURA.md) — come sono fatte le cose e perché
- [Guida utente](docs/GUIDA_UTENTE.md) — dall'immagine alla stampa
- [API](docs/API.md) — riferimento degli endpoint
- [Plugin](docs/PLUGIN.md) — come estendere l'applicazione

---

## Compatibilità

**Slicer**: Bambu Studio · OrcaSlicer · PrusaSlicer · Cura · Anycubic Slicer

**Formati**: STL · OBJ · 3MF (con materiali e colori) · STEP (solido sfaccettato) · GLB

Per la stampa multicolore il formato consigliato è il **3MF**: conserva la
suddivisione in oggetti e l'assegnazione dei colori agli slot AMS.

---

## Test

```bash
cd backend
python -m pytest                 # tutta la suite, compreso l'end-to-end
python -m pytest -m "not slow"   # solo i test rapidi
```

La suite copre il motore mesh, la segmentazione, gli incastri, i controlli di
stampabilità e il percorso completo dall'immagine ai file esportati. Il test
end-to-end usa il generatore locale, quindi non richiede rete né chiavi API.

---

## Prestazioni

Tre dipendenze native cambiano radicalmente i tempi e vengono installate da
`requirements.txt`:

| Pacchetto | Effetto |
|-----------|---------|
| `embreex` | Ray tracing Intel Embree: il controllo degli spessori passa da decine di secondi a frazioni |
| `manifold3d` | Booleane esatte: incastri e tagli sempre manifold |
| `fast-simplification` | Decimazione QEM compilata (esiste comunque un'implementazione interna di riserva) |

Su un modello da 120.000 triangoli la pipeline completa richiede circa un minuto
e mezzo, generazione AI esclusa.

---

## Licenza

Software proprietario. Tutti i diritti riservati.

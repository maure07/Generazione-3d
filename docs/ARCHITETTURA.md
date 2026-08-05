# Architettura

Questo documento descrive come è costruito PrintReady AI e, soprattutto,
**perché** certe scelte sono state fatte in un modo invece che in un altro.

---

## Visione d'insieme

```
┌──────────────────────────────────────────────────────────┐
│  Electron  ·  React + three.js                           │
│  Carica → Descrivi → Genera → Esporta                    │
└───────────────────────┬──────────────────────────────────┘
                        │ REST + WebSocket (127.0.0.1)
┌───────────────────────┴──────────────────────────────────┐
│  FastAPI                                                  │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Pipeline (17 passi)                                │  │
│  │  ai → mesh → segmentation → joinery → ams → export  │  │
│  └────────────────────────────────────────────────────┘  │
│  projects (persistenza, undo/redo)  ·  plugins  ·  OTA   │
└──────────────────────────────────────────────────────────┘
```

Il backend è un processo Python locale avviato da Electron. Non c'è alcun
server remoto: i modelli e i progetti restano sul disco dell'utente.

---

## Perché backend Python separato

Tutto l'ecosistema della geometria computazionale — trimesh, manifold3d,
Open3D, numpy, scipy — vive in Python. Riscrivere quelle capacità in
TypeScript sarebbe stato un lavoro enorme con un risultato inferiore.

Il costo è avviare e sorvegliare un processo figlio. Il vantaggio è poter
usare direttamente algoritmi maturi e ben testati, e poter esporre lo stesso
backend via API per l'elaborazione batch senza interfaccia.

---

## Livelli

Ogni livello dipende solo da quelli sotto di sé.

| Livello | Responsabilità | Dipende da |
|---------|----------------|------------|
| `domain` | Modelli, enumerazioni, bus eventi | — |
| `mesh` | Operazioni geometriche pure | `domain` |
| `ai` | Generazione 3D e analisi del prompt | `domain`, `mesh` |
| `segmentation` | Divisione in pezzi | `mesh`, `ai` |
| `joinery` | Incastri | `mesh`, `segmentation` |
| `printability` | Controlli e correzioni | `mesh` |
| `ams` | Colori multicolore | `mesh`, `segmentation` |
| `exporters` | Scrittura dei file | `mesh`, `domain` |
| `pipeline` | Orchestrazione | tutti i precedenti |
| `api` | HTTP e WebSocket | `pipeline`, `projects` |

Le funzioni di `mesh` sono **pure**: accettano e restituiscono `trimesh.Trimesh`
senza toccare stato globale. È ciò che rende possibile testarle una per una e
ricomporle liberamente in un plugin.

---

## La pipeline

`PipelineOrchestrator` esegue una lista di `PipelineStep`. Ogni passo:

- dichiara il proprio `step_id`;
- decide da sé se deve essere eseguito (`should_run`);
- riceve il `PipelineContext` e restituisce un messaggio in italiano.

I passi geometrici girano in un thread separato (`asyncio.to_thread`) perché
sono CPU-bound: senza quello, l'interfaccia si bloccherebbe per minuti.

### Passi critici e non critici

Alcuni passi sono elencati in `NON_CRITICAL`: se falliscono, la pipeline
prosegue e il risultato è semplicemente meno rifinito. Se fallisce la
generazione AI o l'esportazione, invece, non c'è nulla da consegnare e il lavoro
si interrompe con un messaggio chiaro.

### Avanzamento

Ogni passo pubblica eventi su un bus in-process. Il WebSocket `/ws/jobs` li
inoltra all'interfaccia. Alla riconnessione vengono rispediti gli ultimi eventi,
così ricaricando la finestra non si perde il filo di un lavoro in corso.

---

## Scelte di progetto significative

### Il generatore locale non è un ripiego di facciata

`ai/local.py` implementa un **visual hull per space carving**: costruisce una
griglia di voxel, la scava proiettandola nelle silhouette disponibili e ne
estrae la superficie con marching cubes. Con una sola immagine aggiunge un
profilo di profondità derivato dalla *distance transform* della silhouette, così
il risultato è un volume bombato invece di un prisma piatto.

Non compete con un modello generativo addestrato. Ma è geometria vera, coerente
e stampabile: l'applicazione funziona senza rete e senza abbonamenti, e la
suite di test gira ovunque.

### Segmentazione: la geometria vince sui prior

`segmentation/anatomy.py` percorre il modello dal basso verso l'alto misurando
area e numero di isole di ogni sezione orizzontale. Da quel profilo emergono
caviglie, biforcazione delle gambe, vita, spalle e collo.

I canoni di proporzione (Funko e realistico) servono solo come *prior* per
etichettare i pezzi: se la geometria li contraddice, vince la geometria. Un
modello non è mai esattamente proporzionato come il canone.

Il rilevamento delle braccia merita una nota. Un cercatore di minimi locali non
funziona: le braccia attaccate alle spalle producono un **gradino** nel profilo
dell'area lungo X, non una gola. Si cerca quindi il salto di area, e il
candidato viene **validato tagliando davvero** e misurando il volume del pezzo
esterno. Senza quella validazione, un fianco largo verrebbe amputato come se
fosse un braccio.

### I tagli devono produrre solidi chiusi

`slice_mesh_plane` di trimesh è veloce ma chiude la sezione con una
triangolazione semplice del contorno: dove la sezione è frastagliata o la mesh
ha compenetrazioni, la chiusura fallisce e il pezzo esce aperto — quindi senza
volume, non stampabile e incapace di ricevere incastri.

`_cut_half` prova prima la via veloce e, se il risultato non è stagno, ripiega
sull'**intersezione booleana con una scatola** che rappresenta il semispazio.
È più costoso, ma il motore esatto restituisce sempre un solido valido.

### Incastri: dove, come, quanto grandi

`joinery/planner.py` risponde a tre domande in ordine:

1. **quali pezzi si toccano** — coppie con superficie di contatto reale;
2. **dove va l'incastro** — nel baricentro dell'area di contatto, lungo la
   normale media;
3. **quanto deve essere grande** — dal raggio utile dell'area e dalla profondità
   di materiale disponibile, misurata con un raggio lanciato nel pezzo femmina.

Il raggio utile non è il semplice cerchio equivalente all'area: quello
sovrastima quando il contatto è allungato o spezzato in due impronte. Si usa
anche la dispersione effettiva dei punti di contatto sul piano dell'interfaccia.

Il calcolo delle interfacce sarebbe proibitivo con la distanza esatta
punto-superficie su ogni faccia di ogni coppia. Si procede in due fasi: un
KD-tree seleziona i candidati (esatto sulle superfici di taglio, dove i vertici
coincidono), poi la distanza esatta raffina solo quei pochi.

### Il ruolo di maschio e femmina non è arbitrario

La basetta porta sempre la spina, perché il modello si infila sopra. Fra pezzi
impilati la spina sta su quello inferiore, così il pezzo superiore si cala
dall'alto. A parità di quota, la spina va sul pezzo più grande, che ha più
materiale attorno alla radice e regge meglio lo sforzo.

### Le correzioni automatiche non possono peggiorare

`AutoFixer` applica un correttore, ricalcola il punteggio di stampabilità e
**annulla la modifica se il punteggio scende**. Il rapporto elenca sia le
correzioni applicate sia quelle annullate, con la ragione.

Alcuni problemi non sono correggibili senza tradire l'intento dell'utente: le
isole si risolvono con i supporti dello slicer, un pezzo fuori volume si
risolve riscalando. In quei casi il correttore lo dichiara invece di
intervenire.

### Ispessire non è sempre la risposta

`thicken_thin_walls` sposta i vertici lungo le normali: funziona per correzioni
di qualche decimo di millimetro. Portare una lastra da 0,5 mm a 0,8 mm
richiederebbe uno spostamento paragonabile alla geometria stessa, e la
superficie si ripiegherebbe su se stessa. Lo spostamento è quindi limitato, e
ciò che resta sottile viene riportato all'utente perché riscali il pezzo.

### Misurare lo spessore è meno ovvio di quanto sembri

Lo spessore si misura lanciando un raggio dal centro di ogni faccia verso
l'interno. Due dettagli fanno la differenza fra una misura corretta e una
inutile:

- si parte dal **centro delle facce**, non dai vertici: la normale di faccia è
  esatta, quella di vertice è mediata e su uno spigolo vivo punta in diagonale,
  sovrastimando lo spessore;
- si contano solo gli impatti su facce **affacciate** (normale opposta): senza
  quel filtro, su uno spigolo il raggio colpisce una faccia quasi complanare a
  distanza nulla e ogni pezzo massiccio risulterebbe sottilissimo.

### AMS: separare i colori batte cambiarli

Un cambio filamento costa uno spurgo di oltre cento millimetri cubi. Su un
modello con molti pezzi colorati lo spreco supera il peso del modello.

L'ottimizzazione più efficace è già avvenuta a monte: la segmentazione ha
separato fisicamente i colori. `ams/optimizer.py` sfrutta quel fatto ordinando
la stampa per gruppi di colore, e lo dice esplicitamente nelle note: stampando
un gruppo alla volta, la torre di spurgo non serve affatto.

### STEP è un solido sfaccettato, e va detto

Lo STEP nasce per la geometria analitica. Una mesh generata dall'AI non lo è, e
convertirla in superfici NURBS richiederebbe un reverse engineering che nessun
automatismo fa senza perdere fedeltà.

`exporters/step.py` scrive un **BREP sfaccettato**: STEP valido, apribile in
FreeCAD o Fusion, ma con facce triangolari. Il modulo lo dichiara nella propria
documentazione e il rapporto di esportazione lo ripete all'utente. Oltre 40.000
triangoli la mesh viene decimata, perché ogni triangolo genera una decina di
entità e il file crescerebbe a centinaia di megabyte.

---

## Persistenza e cronologia

Ogni progetto è una cartella con `project.json`, gli snapshot della cronologia,
le immagini caricate e l'anteprima. La scrittura è **atomica** (file temporaneo
più rename): un crash a metà salvataggio non corrompe il progetto.

L'undo/redo è una lista di snapshot con un cursore. Un nuovo snapshot tronca il
ramo "redo", come in qualunque editor. Il salvataggio automatico **non** crea
snapshot: altrimenti riempirebbe la cronologia di voci che l'utente non ha mai
chiesto.

---

## Estensibilità

Un plugin è un modulo Python con una funzione `register(api)`. Attraverso
`PluginAPI` può aggiungere provider AI, regole di stampabilità, esportatori,
passi della pipeline e termini al lessico semantico.

I plugin sono codice eseguito con i privilegi dell'applicazione: il caricatore
li isola l'uno dall'altro (un errore non blocca gli altri) ma non li mette in
sandbox. La documentazione lo dichiara: installare solo plugin di cui ci si fida.

---

## Prestazioni

I due colli di bottiglia scoperti sviluppando, entrambi risolti:

**Ray casting.** Il tracciatore in puro Python di trimesh gestisce qualche
migliaio di raggi al secondo. Con `embreex` si passa a centinaia di migliaia:
il controllo degli spessori scende da decine di secondi a frazioni. Esiste
comunque un tetto al numero di raggi, perché l'applicazione deve restare usabile
anche senza la dipendenza nativa.

**Autointersezioni.** Il test triangolo-triangolo su ogni coppia candidata, in
Python, bloccava l'interfaccia per minuti. L'implementazione attuale scarta le
coppie adiacenti in modo vettoriale e valuta il test di Möller su interi array
numpy: 20.000 facce in poco più di un secondo.

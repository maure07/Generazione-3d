# API

Il backend espone un'API REST e un WebSocket su `http://127.0.0.1:8765`.
La documentazione interattiva generata da FastAPI è su `/docs`.

---

## Flusso tipico

```bash
BASE=http://127.0.0.1:8765

# 1. Crea il progetto
PID=$(curl -s -X POST $BASE/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"Cavaliere","prompt":"cavaliere con spada e mantello"}' \
  | jq -r .id)

# 2. Carica l'immagine
curl -s -X POST $BASE/api/projects/$PID/images -F file=@cavaliere.png

# 3. Avvia la pipeline
JOB=$(curl -s -X POST $BASE/api/generate \
  -H 'Content-Type: application/json' \
  -d "{\"project_id\":\"$PID\"}" | jq -r .job_id)

# 4. Segui l'avanzamento
curl -s $BASE/api/generate/$JOB | jq '{state, progress, message_it}'

# 5. Scarica i file
curl -s $BASE/api/mesh/$JOB/files | jq
```

---

## Progetti

| Metodo | Percorso | Descrizione |
|--------|----------|-------------|
| `GET` | `/api/projects` | Cronologia dei progetti, dal più recente |
| `POST` | `/api/projects` | Crea un progetto |
| `GET` | `/api/projects/{id}` | Dettaglio completo |
| `PATCH` | `/api/projects/{id}` | Modifica nome, descrizione, impostazioni, note |
| `DELETE` | `/api/projects/{id}` | Elimina progetto e file |
| `POST` | `/api/projects/{id}/save` | Salvataggio esplicito |

### Immagini

| Metodo | Percorso | Descrizione |
|--------|----------|-------------|
| `POST` | `/api/projects/{id}/images` | Carica un'immagine (`multipart/form-data`) |
| `DELETE` | `/api/projects/{id}/images/{image_id}` | Rimuove un'immagine |
| `GET` | `/api/projects/{id}/images/{image_id}/file` | Scarica il file |

Formati accettati: PNG, JPEG, WebP, BMP. Dimensione massima 25 MB.
Il parametro `view` (`front`, `back`, `left`, `right`, `top`, `auto`) indica da
quale angolazione è ripreso il soggetto.

### Cronologia

| Metodo | Percorso | Descrizione |
|--------|----------|-------------|
| `GET` | `/api/projects/{id}/history` | Elenco degli snapshot |
| `POST` | `/api/projects/{id}/undo` | Annulla l'ultima modifica |
| `POST` | `/api/projects/{id}/redo` | Ripete la modifica annullata |

`409` significa che non c'è nulla da annullare o ripetere.

---

## Generazione

| Metodo | Percorso | Descrizione |
|--------|----------|-------------|
| `POST` | `/api/generate` | Avvia la pipeline (risposta `202`, immediata) |
| `POST` | `/api/generate/batch` | Avvia più progetti in parallelo |
| `GET` | `/api/generate/jobs` | Elenco dei job |
| `GET` | `/api/generate/{job_id}` | Stato di un job |
| `GET` | `/api/generate/{job_id}/report` | Rapporto completo (job concluso) |
| `POST` | `/api/generate/{job_id}/cancel` | Annulla un job in corso |
| `GET` | `/api/generate/providers/list` | Provider AI e stato di configurazione |

### Corpo della richiesta

```json
{
  "project_id": "abc123",
  "settings": null,
  "steps_from": null
}
```

`settings` sovrascrive le impostazioni del progetto. `steps_from` riprende la
pipeline da un passo specifico, utile per rigenerare solo gli incastri senza
ripetere la generazione AI.

### Stato del job

```json
{
  "job_id": "…",
  "state": "running",
  "current_step": "segmentation",
  "progress": 0.76,
  "message_it": "Segmentazione intelligente",
  "report": null,
  "error_it": null
}
```

Stati possibili: `pending`, `running`, `completed`, `failed`, `cancelled`.

---

## Risultati

| Metodo | Percorso | Descrizione |
|--------|----------|-------------|
| `GET` | `/api/mesh/{job_id}/preview` | GLB combinato per l'anteprima 3D |
| `GET` | `/api/mesh/{job_id}/files` | Elenco dei file prodotti |
| `GET` | `/api/mesh/{job_id}/parts/{part_id}/file?fmt=stl` | Scarica un singolo pezzo |
| `GET` | `/api/mesh/{job_id}/instructions` | Istruzioni di montaggio (Markdown) |
| `POST` | `/api/mesh/analyze?path=…` | Analizza un file già su disco |

`POST /api/mesh/analyze` accetta solo percorsi **dentro la cartella dati**
dell'applicazione: non è un lettore di file arbitrari del sistema.

---

## Impostazioni

| Metodo | Percorso | Descrizione |
|--------|----------|-------------|
| `GET` | `/api/settings` | Stato dell'applicazione e capacità disponibili |
| `GET` | `/api/settings/defaults` | Impostazioni di generazione predefinite |
| `GET` | `/api/settings/printers` | Profili stampante disponibili |
| `GET` | `/api/settings/printers/{preset}` | Un singolo profilo |
| `GET` | `/api/settings/options` | Enumerazioni con le etichette italiane |
| `GET` | `/api/settings/tolerance/{valore}` | Classe di accoppiamento di una tolleranza |
| `GET` | `/api/settings/providers` | Stato dei provider AI |
| `GET` | `/api/settings/plugins` | Plugin installati |
| `POST` | `/api/settings/plugins/reload` | Ricarica i plugin senza riavviare |
| `GET` | `/api/settings/updates/check` | Controlla gli aggiornamenti |

Le chiavi API **non vengono mai restituite**: `/api/settings` espone solo un
flag che dice se ogni provider è configurato.

---

## WebSocket

```
ws://127.0.0.1:8765/ws/jobs?job_id=<id>
```

Parametri facoltativi: `job_id`, `project_id`. Senza filtri arrivano gli eventi
di tutti i job.

Alla connessione vengono rispediti gli ultimi eventi, così ricaricando
l'interfaccia non si perde il filo di un lavoro in corso.

### Eventi

| Tipo | Quando | Contenuto principale |
|------|--------|----------------------|
| `job_started` | Avvio della pipeline | `total_steps` |
| `step_started` | Inizio di un passo | `step`, `label_it`, `progress` |
| `step_progress` | Avanzamento nel passo | `message_it` |
| `step_completed` | Passo concluso | `message_it`, `duration_s` |
| `step_failed` | Passo fallito | `error_it` |
| `job_finished` | Fine del lavoro | `state`, `summary_it`, `printability_score` |
| `ping` | Ogni 25 secondi | — |

```json
{
  "type": "step_completed",
  "job_id": "…",
  "payload": {
    "step": "joinery",
    "label_it": "Creazione incastri",
    "message_it": "Creati 6 incastri (Spina cilindrica)",
    "duration_s": 1.6
  },
  "timestamp": "2026-08-05T14:22:31+00:00"
}
```

---

## Codici di errore

| Codice | Significato |
|--------|-------------|
| `400` | Richiesta incompleta (per esempio: generazione senza immagini) |
| `404` | Progetto, job o file inesistente |
| `409` | Operazione non applicabile allo stato corrente |
| `413` | Immagine troppo grande |
| `415` | Formato immagine non supportato |
| `422` | File 3D illeggibile o parametro fuori intervallo |
| `500` | Errore interno (i dettagli sono nel log dell'applicazione) |
| `502` | Un provider AI esterno non risponde |

Il campo `detail` contiene sempre un messaggio in italiano già leggibile
dall'utente finale.

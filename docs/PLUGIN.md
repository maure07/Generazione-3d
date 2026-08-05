# Plugin

PrintReady AI si estende con moduli Python caricati all'avvio. Un plugin può
aggiungere generatori 3D, regole di controllo, esportatori, passi della pipeline
e termini al lessico di riconoscimento delle parti.

---

## Dove si installano

I plugin vanno nella cartella dati dell'applicazione:

| Sistema | Percorso |
|---------|----------|
| Windows | `%LOCALAPPDATA%\PrintReadyAI\plugins\` |
| macOS | `~/Library/Application Support/PrintReadyAI/plugins/` |
| Linux | `~/.local/share/PrintReadyAI/plugins/` |

Un plugin può essere un singolo file `.py` oppure una cartella con
`__init__.py`. Vengono ricaricati con `POST /api/settings/plugins/reload`, senza
riavviare l'applicazione.

> **Attenzione.** I plugin sono codice Python eseguito con i privilegi
> dell'applicazione. Il caricatore li isola l'uno dall'altro — un errore in uno
> non impedisce il caricamento degli altri — ma non li mette in sandbox.
> Installare solo plugin di cui ci si fida.

---

## Struttura minima

```python
PLUGIN_NAME = "Il mio plugin"
PLUGIN_VERSION = "1.0"
PLUGIN_AUTHOR = "Nome Cognome"
PLUGIN_DESCRIPTION_IT = "Che cosa fa, in una riga"


def register(api):
    """Punto d'ingresso richiesto dal caricatore."""
    api.log("Plugin caricato")
```

Solo `register(api)` è obbligatoria. I metadati compaiono nell'interfaccia e
in `GET /api/settings/plugins`.

---

## Che cosa si può aggiungere

### Termini al lessico semantico

Il modo più semplice per migliorare la segmentazione nel proprio dominio.

```python
from printready.domain.enums import PartType


def register(api):
    api.add_semantic_terms(
        PartType.WEAPON,
        {"bastone runico", "grimorio", "falcione", "balestra"},
    )
    api.add_semantic_terms(
        PartType.ACCESSORY,
        {"faretra", "borraccia", "lanterna", "tomo"},
    )
```

Da quel momento un prompt che cita «mago con grimorio» farà cercare
all'applicazione un elemento da separare come arma.

### Regole di stampabilità

Una regola riceve la mesh e il profilo stampante e restituisce una lista di
`Issue`.

```python
import numpy as np

from printready.domain.enums import IssueCode, Severity
from printready.domain.models import Issue


def rule_base_stability(mesh, printer):
    """Segnala un appoggio troppo piccolo sul piatto."""
    z_min = float(mesh.bounds[0][2])
    on_plate = np.abs(np.asarray(mesh.triangles_center)[:, 2] - z_min) < printer.layer_height_mm
    contact = float(np.asarray(mesh.area_faces)[on_plate].sum())

    footprint = float(mesh.extents[0] * mesh.extents[1])
    if footprint <= 0 or contact / footprint >= 0.02:
        return []

    return [
        Issue(
            code=IssueCode.ISLAND,
            severity=Severity.WARNING,
            message_it=f"Appoggio di soli {contact:.1f} mm²: aggiungere un brim",
        )
    ]


def register(api):
    api.add_printability_rule(IssueCode.ISLAND, rule_base_stability)
```

La regola entra automaticamente nell'analisi e nel punteggio.

### Provider di generazione 3D

Per collegare un servizio o un modello locale non ancora supportato.

```python
from printready.ai.base import GenerationResult, Image3DProvider


class MioProvider(Image3DProvider):
    name = "mio_servizio"
    label_it = "Il mio servizio (cloud)"
    supports_color = True
    max_images = 4

    def is_available(self):
        return True

    async def generate(self, request, progress=None):
        self._report(progress, 0.1, "Invio della richiesta")
        percorso = ...  # produce un file 3D
        self._report(progress, 1.0, "Modello pronto")
        return GenerationResult(mesh_path=percorso, provider=self.name)


def register(api):
    api.add_ai_provider(MioProvider())
```

Il provider diventa selezionabile nelle impostazioni ed entra nella catena di
ricaduta automatica.

### Esportatori

```python
from printready.domain.enums import ExportFormat
from printready.exporters.base import Exporter


class AmfExporter(Exporter):
    format = ExportFormat.OBJ  # oppure un formato registrato dal plugin
    supports_color = True

    def export(self, items, destination, combined=False):
        destination = self._prepare(destination)
        prodotti = []
        for item in items:
            percorso = destination / f"{item.name}.amf"
            ...  # scrittura del file
            prodotti.append(self._describe(percorso, item.part_id, combined=False))
        return prodotti


def register(api):
    api.add_exporter(ExportFormat.OBJ, AmfExporter)
```

### Passi della pipeline

```python
from printready.domain.enums import StepId
from printready.pipeline.steps import PipelineStep


class IncisioneLogo(PipelineStep):
    step_id = StepId.EXPORT  # riusa un identificatore esistente
    critical = False

    def should_run(self, context):
        return "logo" in context.project.prompt.lower()

    def run(self, context):
        # context.mesh e context.parts sono a disposizione
        return "Logo inciso sulla basetta", {"pezzi_modificati": 1}


def register(api):
    api.add_pipeline_step(StepId.JOINERY, IncisioneLogo)
```

Il passo viene inserito subito dopo quello indicato.

### Ascoltatori di eventi

```python
def register(api):
    def su_completamento(event):
        api.log(f"Lavoro concluso: {event.payload.get('summary_it')}")

    api.on_event("job_finished", su_completamento)
```

---

## Servizi a disposizione del plugin

| Metodo | Che cosa restituisce |
|--------|----------------------|
| `api.log(messaggio)` | Scrive nel log applicativo, con il prefisso del plugin |
| `api.data_dir()` | Cartella dedicata dove salvare i dati del plugin |
| `api.settings()` | Impostazioni globali (sola lettura) |

---

## Esempio completo

Il file
[`backend/printready/plugins/builtin/esempio_incastri.py`](../backend/printready/plugins/builtin/esempio_incastri.py)
è un plugin funzionante e commentato: estende il lessico con termini fantasy,
aggiunge un controllo di stabilità sul piatto e mostra come costruire una spina
a coda di rondine.

Copialo nella cartella dei plugin per vederlo caricato all'avvio.

---

## Verifica del caricamento

```bash
curl -s http://127.0.0.1:8765/api/settings/plugins | jq
```

```json
[
  {
    "name": "Esempio incastri e lessico",
    "version": "1.0",
    "enabled": true,
    "error_it": null,
    "contributions": [
      "5 termini per «weapon»",
      "5 termini per «accessory»",
      "regola di stampabilità «island»"
    ]
  }
]
```

Il campo `contributions` elenca esattamente che cosa il plugin ha registrato: se
è vuoto, `register()` non ha aggiunto nulla. Se `error_it` è valorizzato, il
plugin non è stato caricato e il messaggio dice perché.

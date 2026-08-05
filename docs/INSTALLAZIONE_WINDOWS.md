# Installazione su Windows

Guida passo passo per provare PrintReady AI su Windows 10 o 11 a 64 bit.

Tempo richiesto: circa 15 minuti, quasi tutti di attesa per i download.

---

## 1. Installare Python

Scarica Python 3.11 o 3.12 da [python.org/downloads](https://www.python.org/downloads/).

> **Durante l'installazione spunta «Add python.exe to PATH».**
> È la casella in fondo alla prima schermata. Se la salti, il resto non
> funzionerà e dovrai reinstallare.

Verifica aprendo il **Prompt dei comandi** (tasto Windows → digita `cmd`):

```cmd
python --version
```

Deve rispondere `Python 3.11.x` o simile.

---

## 2. Installare Node.js

Scarica la versione **LTS** da [nodejs.org](https://nodejs.org/). Installazione
standard, nessuna opzione da cambiare.

Verifica:

```cmd
node --version
npm --version
```

---

## 3. Scaricare il progetto

Se hai Git installato:

```cmd
git clone -b claude/printready-ai-desktop-app-u4hi7a https://github.com/maure07/Generazione-3d.git
cd Generazione-3d
```

Altrimenti scarica lo ZIP da GitHub (pulsante verde **Code** → **Download ZIP**),
estrailo, e apri il Prompt dei comandi nella cartella estratta.

---

## 4. Preparare il motore di elaborazione

```cmd
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

L'ultimo comando scarica circa 400 MB e richiede qualche minuto. Sono tutte
librerie con pacchetti precompilati per Windows: non serve alcun compilatore.

Verifica che tutto sia a posto:

```cmd
python -m printready
```

Dovresti vedere:

```
PrintReady AI 1.0.0 — http://127.0.0.1:8765
Documentazione API: http://127.0.0.1:8765/docs
```

Apri <http://127.0.0.1:8765/docs> nel browser: se vedi la documentazione
dell'API, il motore funziona. Chiudi con <kbd>Ctrl</kbd>+<kbd>C</kbd>.

---

## 5. Preparare l'interfaccia

Apri un **nuovo** Prompt dei comandi nella cartella del progetto:

```cmd
cd frontend
npm install
```

Altri due o tre minuti di download.

---

## 6. Avviare l'applicazione

```cmd
npm run dev
```

Si apre la finestra di PrintReady AI. Il motore di elaborazione parte da solo:
non serve tenere aperto il comando del passo 4.

In alternativa, dalla cartella principale del progetto:

```powershell
.\scripts\avvia.ps1
```

Lo script crea l'ambiente virtuale e installa tutto da solo, se non l'hai già
fatto.

---

## 7. Prima prova

1. Nel pannello di sinistra scrivi un nome e premi **Crea**.
2. Trascina un'immagine nella zona di caricamento — va benissimo la foto di un
   pupazzo, un disegno o uno screenshot di un personaggio.
3. Scrivi una descrizione, per esempio
   `personaggio funko con cappello e scarpe`.
4. Premi **Genera modello 3D** e osserva il registro.

Dopo un minuto o due comparirà l'anteprima 3D con i pezzi separati, gli
incastri e i file pronti.

> **Nota sulla qualità.** Senza chiavi API l'applicazione usa il generatore
> locale, che ricostruisce il volume dalle silhouette. Produce geometria
> corretta e stampabile, ma non ha la resa di un modello generativo addestrato.
> Serve a provare l'intera catena — riparazione, segmentazione, incastri,
> colori, esportazione — senza dipendere da un servizio esterno.
> Per risultati migliori vedi il passo 8.

Le immagini che funzionano meglio hanno il soggetto ben staccato dallo sfondo,
figura intera e vista frontale. Un PNG con sfondo trasparente è il caso ideale.

---

## 8. Facoltativo: collegare un generatore 3D di qualità

Crea un file `backend\.env` (puoi copiare `backend\.env.example`) con la tua
chiave:

```ini
PRINTREADY_TRIPO_API_KEY=la_tua_chiave
```

oppure

```ini
PRINTREADY_MESHY_API_KEY=la_tua_chiave
```

Riavvia l'applicazione. Con `ai_provider` impostato su `auto` (il valore
predefinito) il servizio configurato viene usato per primo, con ricaduta
automatica sul generatore locale se non risponde.

I client per Tripo, Meshy e Hunyuan3D sono scritti sui rispettivi protocolli
documentati ma **non sono stati provati contro i servizi reali**, perché non
avevo credenziali. Se qualcosa non torna, l'errore che vedrai sarà esplicito e
il generatore locale interverrà comunque.

---

## Dove finiscono i file

Progetti, modelli esportati e log stanno in:

```
%LOCALAPPDATA%\PrintReadyAI\
```

Incolla quel percorso nella barra di Esplora file per aprirlo. Dentro trovi
`projects\`, `exports\` e `logs\printready.log`.

Dal pannello dei risultati il pulsante **Apri la cartella dei file** ci porta
direttamente.

---

## Problemi frequenti

**«python non è riconosciuto come comando»**
Python non è nel PATH. Reinstallalo spuntando «Add python.exe to PATH», oppure
usa il percorso completo (di solito
`%LOCALAPPDATA%\Programs\Python\Python312\python.exe`).

**«Manca la libreria …»** all'avvio
L'ambiente virtuale non è stato creato o le dipendenze non sono state
installate. Ripeti il passo 4. Il messaggio dell'applicazione riporta i comandi
esatti da eseguire.

**Windows Defender o SmartScreen blocca l'avvio**
Succede con le applicazioni non firmate. Scegli «Ulteriori informazioni» →
«Esegui comunque». Se preferisci non farlo, puoi usare solo il backend dal
passo 4 e lavorare via API o dalla pagina `/docs`.

**Il firewall chiede il permesso di rete**
Puoi negarlo: il backend ascolta solo su `127.0.0.1`, cioè sul computer stesso.

**La generazione è lenta**
Il generatore locale lavora su una griglia di voxel. Una figura tipica richiede
circa 30 secondi per la generazione più un minuto per tutta la catena di
ottimizzazione. Con un provider cloud i tempi si spostano sulla rete.

**L'anteprima 3D resta nera**
Serve un'accelerazione grafica funzionante. Aggiorna i driver della scheda
video. I file esportati vengono prodotti comunque: li trovi nella cartella
indicata sopra.

---

## Creare l'installatore .exe

Se vuoi un pacchetto installabile invece di avviare da riga di comando:

```cmd
cd frontend
npm run dist
```

Il risultato è `frontend\release\PrintReadyAI-1.0.0-win64.exe`.

La configurazione di `electron-builder` è già pronta e include il backend fra
le risorse. **Questo passo non è mai stato eseguito** durante lo sviluppo,
perché l'ambiente disponibile era Linux: mettici in conto qualche aggiustamento
al primo tentativo.

Il percorso `npm run dev` del passo 6, invece, è stato provato e funziona.

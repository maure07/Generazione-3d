/**
 * Processo principale di Electron.
 *
 * Responsabilità:
 *  - avviare il backend Python come processo figlio e attenderne la prontezza;
 *  - creare la finestra dell'applicazione;
 *  - spegnere ordinatamente il backend alla chiusura.
 *
 * In sviluppo il backend può essere già in esecuzione: in quel caso non viene
 * riavviato, così `npm run dev` non entra in conflitto con un server manuale.
 */
import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const IS_DEV = process.env.NODE_ENV === 'development';
const BACKEND_HOST = '127.0.0.1';
const BACKEND_PORT = Number(process.env.PRINTREADY_PORT ?? 8765);
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;
/** Tempo massimo di attesa per l'avvio del backend, in millisecondi. */
const BACKEND_TIMEOUT_MS = 90_000;

let mainWindow: BrowserWindow | null = null;
let backend: ChildProcess | null = null;

/** Percorso della cartella del backend, diverso fra sviluppo e app installata. */
function backendDir(): string {
  return IS_DEV
    ? path.join(__dirname, '..', '..', 'backend')
    : path.join(process.resourcesPath, 'backend');
}

/**
 * Interprete Python da usare.
 *
 * Si cerca un ambiente virtuale prima dentro `backend/` (come indica il README)
 * e poi nella radice del progetto, perché è una collocazione altrettanto
 * comune. Solo in ultima istanza si ricade sul Python di sistema, che quasi
 * certamente non ha le dipendenze installate: in quel caso il fallimento va
 * spiegato bene all'utente, ed è ciò che fa `startBackend`.
 */
function pythonExecutable(): { command: string; fromVenv: boolean } {
  const dir = backendDir();
  const root = path.join(dir, '..');
  const relative =
    process.platform === 'win32'
      ? path.join('.venv', 'Scripts', 'python.exe')
      : path.join('.venv', 'bin', 'python');

  for (const base of [dir, root]) {
    const candidate = path.join(base, relative);
    if (existsSync(candidate)) return { command: candidate, fromVenv: true };
  }

  return { command: process.platform === 'win32' ? 'python' : 'python3', fromVenv: false };
}

/** Verifica se il backend risponde già. */
async function backendIsUp(): Promise<boolean> {
  try {
    const response = await fetch(`${BACKEND_URL}/api/health`, {
      signal: AbortSignal.timeout(1500),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/** Estrae dal traceback di Python il motivo comprensibile del fallimento. */
function explainBackendFailure(output: string, fromVenv: boolean): string {
  const mancante = output.match(/ModuleNotFoundError: No module named '([^']+)'/);
  if (mancante) {
    const dove = fromVenv
      ? "nell'ambiente virtuale"
      : 'nel Python di sistema (non è stato trovato alcun ambiente virtuale)';
    return (
      `Manca la libreria «${mancante[1]}» ${dove}.\n\n` +
      'Aprire un terminale nella cartella backend ed eseguire:\n\n' +
      (process.platform === 'win32'
        ? '    python -m venv .venv\n    .venv\\Scripts\\activate\n    pip install -r requirements.txt'
        : '    python3 -m venv .venv\n    source .venv/bin/activate\n    pip install -r requirements.txt')
    );
  }

  if (/ENOENT|not found|non trovato/i.test(output)) {
    return (
      'Python non è stato trovato.\n\n' +
      'Installare Python 3.10 o successivo e assicurarsi che sia nel PATH ' +
      '(durante l\'installazione spuntare «Add Python to PATH»).'
    );
  }

  const righe = output.trim().split('\n').slice(-12).join('\n');
  return righe
    ? `Il motore di elaborazione si è chiuso con questo errore:\n\n${righe}`
    : 'Il motore di elaborazione si è chiuso senza spiegazioni. Consultare il log dell\'applicazione.';
}

/** Avvia il backend e attende che risponda. */
async function startBackend(): Promise<void> {
  if (await backendIsUp()) {
    console.log('Backend già attivo: non lo riavvio');
    return;
  }

  const { command, fromVenv } = pythonExecutable();
  console.log(`Avvio del backend: ${command} -m printready --port ${BACKEND_PORT}`);
  if (!fromVenv) {
    console.warn(
      'Nessun ambiente virtuale trovato: uso il Python di sistema, che potrebbe non avere le dipendenze',
    );
  }

  // Le ultime righe di output servono a spiegare un eventuale fallimento:
  // senza di esse l'utente vedrebbe solo un timeout senza causa.
  let output = '';
  let exited: number | null = null;

  backend = spawn(command, ['-m', 'printready', '--port', String(BACKEND_PORT)], {
    cwd: backendDir(),
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  const raccogli = (data: Buffer) => {
    const testo = String(data);
    output = (output + testo).slice(-4000);
    process.stderr.write(`[backend] ${testo}`);
  };
  backend.stdout?.on('data', raccogli);
  backend.stderr?.on('data', raccogli);

  backend.on('error', (error) => {
    output += `\n${error.message}`;
    exited = -1;
  });
  backend.on('exit', (code) => {
    console.log(`Backend terminato con codice ${code}`);
    exited = code ?? -1;
    backend = null;
  });

  const deadline = Date.now() + BACKEND_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await backendIsUp()) {
      console.log('Backend pronto');
      return;
    }
    // Se il processo è già morto è inutile attendere il timeout: si può dire
    // subito all'utente che cosa è andato storto.
    if (exited !== null) {
      throw new Error(explainBackendFailure(output, fromVenv));
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(
    'Il motore di elaborazione non ha risposto entro ' +
      `${Math.round(BACKEND_TIMEOUT_MS / 1000)} secondi.\n\n` +
      explainBackendFailure(output, fromVenv),
  );
}

/** Arresta il backend, prima con garbo poi con decisione. */
function stopBackend(): void {
  if (!backend) return;
  console.log('Arresto del backend');
  backend.kill('SIGTERM');
  const child = backend;
  setTimeout(() => {
    if (child && !child.killed) child.kill('SIGKILL');
  }, 4000);
  backend = null;
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#0f1115',
    show: false,
    title: 'PrintReady AI',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow?.show());

  // I collegamenti esterni si aprono nel browser, non dentro l'app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  if (IS_DEV) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/** Canali IPC esposti al renderer tramite il preload. */
function registerIpc(): void {
  ipcMain.handle('app:backend-url', () => BACKEND_URL);
  ipcMain.handle('app:version', () => app.getVersion());

  ipcMain.handle('app:open-path', async (_event, target: string) => {
    const error = await shell.openPath(target);
    return error === '' ? { ok: true } : { ok: false, error };
  });

  ipcMain.handle('app:show-in-folder', (_event, target: string) => {
    shell.showItemInFolder(target);
    return { ok: true };
  });

  ipcMain.handle('app:choose-images', async () => {
    if (!mainWindow) return [];
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Seleziona le immagini del soggetto',
      properties: ['openFile', 'multiSelections'],
      filters: [{ name: 'Immagini', extensions: ['png', 'jpg', 'jpeg', 'webp', 'bmp'] }],
    });
    return result.canceled ? [] : result.filePaths;
  });

  ipcMain.handle('app:choose-folder', async () => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Scegli la cartella di destinazione',
      properties: ['openDirectory', 'createDirectory'],
    });
    return result.canceled ? null : result.filePaths[0];
  });
}

app.whenReady().then(async () => {
  registerIpc();

  try {
    await startBackend();
  } catch (error) {
    dialog.showErrorBox(
      'Avvio non riuscito',
      error instanceof Error ? error.message : String(error),
    );
    app.quit();
    return;
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', stopBackend);
process.on('exit', stopBackend);

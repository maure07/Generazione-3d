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

/** Interprete Python da usare: quello del venv incluso, altrimenti quello di sistema. */
function pythonExecutable(): string {
  const dir = backendDir();
  const candidates =
    process.platform === 'win32'
      ? [path.join(dir, '.venv', 'Scripts', 'python.exe'), 'python']
      : [path.join(dir, '.venv', 'bin', 'python'), 'python3'];

  for (const candidate of candidates) {
    if (candidate.includes(path.sep) && existsSync(candidate)) return candidate;
  }
  return candidates[candidates.length - 1];
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

/** Avvia il backend e attende che risponda. */
async function startBackend(): Promise<void> {
  if (await backendIsUp()) {
    console.log('Backend già attivo: non lo riavvio');
    return;
  }

  const python = pythonExecutable();
  console.log(`Avvio del backend: ${python} -m printready --port ${BACKEND_PORT}`);

  backend = spawn(python, ['-m', 'printready', '--port', String(BACKEND_PORT)], {
    cwd: backendDir(),
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  backend.stdout?.on('data', (data) => process.stdout.write(`[backend] ${data}`));
  backend.stderr?.on('data', (data) => process.stderr.write(`[backend] ${data}`));
  backend.on('exit', (code) => {
    console.log(`Backend terminato con codice ${code}`);
    backend = null;
  });

  const deadline = Date.now() + BACKEND_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await backendIsUp()) {
      console.log('Backend pronto');
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(
    "Il motore di elaborazione non si è avviato entro il tempo previsto. " +
      'Verificare che Python e le dipendenze siano installati correttamente.',
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

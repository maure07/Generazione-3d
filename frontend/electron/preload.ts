/**
 * Ponte sicuro fra il processo principale e l'interfaccia.
 *
 * Il renderer non ha accesso a Node: può usare solo le funzioni esposte qui,
 * che sono poche e specifiche. È la configurazione raccomandata da Electron
 * per non esporre il filesystem a codice della pagina.
 */
import { contextBridge, ipcRenderer } from 'electron';

const api = {
  /** Indirizzo del backend locale (es. http://127.0.0.1:8765). */
  backendUrl: (): Promise<string> => ipcRenderer.invoke('app:backend-url'),

  /** Versione dell'applicazione installata. */
  version: (): Promise<string> => ipcRenderer.invoke('app:version'),

  /** Apre un file o una cartella con l'applicazione predefinita del sistema. */
  openPath: (target: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('app:open-path', target),

  /** Mostra un file nell'esplora risorse, evidenziandolo. */
  showInFolder: (target: string): Promise<{ ok: boolean }> =>
    ipcRenderer.invoke('app:show-in-folder', target),

  /** Apre la finestra di selezione delle immagini. */
  chooseImages: (): Promise<string[]> => ipcRenderer.invoke('app:choose-images'),

  /** Apre la finestra di selezione di una cartella. */
  chooseFolder: (): Promise<string | null> => ipcRenderer.invoke('app:choose-folder'),
};

contextBridge.exposeInMainWorld('printready', api);

export type PrintReadyBridge = typeof api;

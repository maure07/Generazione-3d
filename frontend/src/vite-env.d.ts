/// <reference types="vite/client" />

/** Funzioni esposte da Electron tramite il preload. */
interface PrintReadyBridge {
  backendUrl(): Promise<string>;
  version(): Promise<string>;
  openPath(target: string): Promise<{ ok: boolean; error?: string }>;
  showInFolder(target: string): Promise<{ ok: boolean }>;
  chooseImages(): Promise<string[]>;
  chooseFolder(): Promise<string | null>;
}

interface Window {
  /** Presente solo quando l'app gira dentro Electron. */
  printready?: PrintReadyBridge;
}

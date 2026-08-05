import React from 'react';
import ReactDOM from 'react-dom/client';

import App from './App';
import { setBaseUrl } from './api/client';
import './styles/app.css';

/**
 * Avvio dell'interfaccia.
 *
 * Quando gira dentro Electron l'indirizzo del backend arriva dal processo
 * principale; nel browser (sviluppo) si usa il valore predefinito.
 */
async function bootstrap(): Promise<void> {
  const bridge = (window as unknown as { printready?: { backendUrl(): Promise<string> } })
    .printready;

  if (bridge) {
    try {
      setBaseUrl(await bridge.backendUrl());
    } catch {
      // Si prosegue con l'indirizzo predefinito.
    }
  }

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void bootstrap();

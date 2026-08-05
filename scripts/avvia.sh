#!/usr/bin/env bash
# Avvio di PrintReady AI in sviluppo (Linux e macOS).
#
#   ./scripts/avvia.sh                avvia backend e interfaccia
#   ./scripts/avvia.sh --solo-backend avvia solo il backend
#
# Alla prima esecuzione crea l'ambiente virtuale e installa le dipendenze.

set -euo pipefail

RADICE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$RADICE/backend"
FRONTEND="$RADICE/frontend"
VENV="$BACKEND/.venv"

scrivi() { printf '\033[36m[PrintReady]\033[0m %s\n' "$1"; }

# --- Backend ---------------------------------------------------------------
if [ ! -d "$VENV" ]; then
    scrivi "Creazione dell'ambiente virtuale Python..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    scrivi "Installazione delle dipendenze (può richiedere qualche minuto)..."
    "$VENV/bin/pip" install -r "$BACKEND/requirements.txt"
fi

if [ "${1:-}" = "--solo-backend" ]; then
    scrivi "Avvio del backend su http://127.0.0.1:8765"
    cd "$BACKEND"
    exec "$VENV/bin/python" -m printready
fi

# --- Interfaccia -----------------------------------------------------------
if [ ! -d "$FRONTEND/node_modules" ]; then
    scrivi "Installazione delle dipendenze Node..."
    (cd "$FRONTEND" && npm install)
fi

scrivi "Avvio dell'applicazione (il backend parte da solo)"
cd "$FRONTEND"
exec npm run dev

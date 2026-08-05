# Avvio di PrintReady AI in sviluppo (Windows PowerShell).
#
#   .\scripts\avvia.ps1              avvia backend e interfaccia
#   .\scripts\avvia.ps1 -SoloBackend avvia solo il backend
#
# Alla prima esecuzione crea l'ambiente virtuale e installa le dipendenze.

param(
    [switch]$SoloBackend
)

$ErrorActionPreference = 'Stop'
$Radice = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Radice 'backend'
$Frontend = Join-Path $Radice 'frontend'

function Scrivi($messaggio) {
    Write-Host "[PrintReady] $messaggio" -ForegroundColor Cyan
}

# --- Backend ---------------------------------------------------------------
$Venv = Join-Path $Backend '.venv'
if (-not (Test-Path $Venv)) {
    Scrivi 'Creazione dell''ambiente virtuale Python...'
    python -m venv $Venv
    & (Join-Path $Venv 'Scripts\python.exe') -m pip install --quiet --upgrade pip
    Scrivi 'Installazione delle dipendenze (può richiedere qualche minuto)...'
    & (Join-Path $Venv 'Scripts\pip.exe') install -r (Join-Path $Backend 'requirements.txt')
}

if ($SoloBackend) {
    Scrivi 'Avvio del backend su http://127.0.0.1:8765'
    Push-Location $Backend
    try {
        & (Join-Path $Venv 'Scripts\python.exe') -m printready
    } finally {
        Pop-Location
    }
    return
}

# --- Interfaccia -----------------------------------------------------------
if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
    Scrivi 'Installazione delle dipendenze Node...'
    Push-Location $Frontend
    try { npm install } finally { Pop-Location }
}

Scrivi 'Avvio dell''applicazione (il backend parte da solo)'
Push-Location $Frontend
try { npm run dev } finally { Pop-Location }

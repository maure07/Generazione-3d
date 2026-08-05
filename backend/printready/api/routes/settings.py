"""Endpoint di configurazione: profili stampante, tolleranze, provider, plugin, OTA."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ...ai.registry import registry
from ...config import get_settings
from ...domain.enums import (
    ExportFormat,
    JoineryType,
    PartType,
    SlicerTarget,
    StepId,
    UIMode,
)
from ...domain.models import GenerationSettings, PrinterProfile
from ...exporters.slicers import SLICER_PROFILES
from ...joinery.tolerance import FIT_CLASSES, fit_class_for
from ...plugins import plugin_manager
from ...printability.rules import RULE_INFO
from ...updates import update_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["impostazioni"])


#: Profili stampante predefiniti offerti all'utente.
PRINTER_PRESETS: dict[str, PrinterProfile] = {
    "bambu_x1c": PrinterProfile(
        name="Bambu Lab X1 Carbon",
        nozzle_diameter_mm=0.4,
        layer_height_mm=0.2,
        bed_size_mm=(256.0, 256.0, 256.0),
        has_ams=True,
        ams_slots=4,
        purge_volume_mm3=140.0,
    ),
    "bambu_a1": PrinterProfile(
        name="Bambu Lab A1",
        nozzle_diameter_mm=0.4,
        layer_height_mm=0.2,
        bed_size_mm=(256.0, 256.0, 256.0),
        has_ams=True,
        ams_slots=4,
        purge_volume_mm3=120.0,
    ),
    "prusa_mk4": PrinterProfile(
        name="Prusa MK4",
        nozzle_diameter_mm=0.4,
        layer_height_mm=0.2,
        bed_size_mm=(250.0, 210.0, 220.0),
        has_ams=True,
        ams_slots=5,
        purge_volume_mm3=180.0,
    ),
    "ender3": PrinterProfile(
        name="Creality Ender 3",
        nozzle_diameter_mm=0.4,
        layer_height_mm=0.2,
        bed_size_mm=(220.0, 220.0, 250.0),
        has_ams=False,
        ams_slots=1,
        purge_volume_mm3=0.0,
    ),
    "anycubic_kobra": PrinterProfile(
        name="Anycubic Kobra 2",
        nozzle_diameter_mm=0.4,
        layer_height_mm=0.2,
        bed_size_mm=(220.0, 220.0, 250.0),
        has_ams=False,
        ams_slots=1,
        purge_volume_mm3=0.0,
    ),
}


@router.get("", summary="Impostazioni e capacità dell'applicazione")
def read_settings() -> dict:
    """Stato della configurazione, senza mai esporre le chiavi API."""
    settings = get_settings()
    return {
        "app": {
            "nome": settings.app_name,
            "versione": settings.version,
            "cartella_dati": str(settings.data_dir),
        },
        "provider": settings.configured_providers(),
        "calcolo": {
            "gpu_abilitata": settings.use_gpu,
            "job_paralleli": settings.max_parallel_jobs,
            "motore_booleano": _boolean_engine(),
        },
        "funzioni": {
            "plugin": settings.enable_plugins,
            "aggiornamenti": settings.enable_ota,
            "autosalvataggio_s": settings.autosave_interval_s,
            "cronologia_illimitata": settings.history_limit == 0,
        },
    }


@router.get("/defaults", response_model=GenerationSettings, summary="Impostazioni predefinite")
def read_defaults() -> GenerationSettings:
    """Impostazioni di generazione predefinite, usate dai nuovi progetti."""
    return GenerationSettings()


@router.get("/printers", summary="Profili stampante disponibili")
def list_printers() -> dict[str, dict]:
    """Profili predefiniti, pronti da applicare a un progetto."""
    return {
        key: {
            **profile.model_dump(mode="json"),
            "parete_minima_effettiva_mm": round(profile.min_printable_wall, 3),
        }
        for key, profile in PRINTER_PRESETS.items()
    }


@router.get("/printers/{preset}", response_model=PrinterProfile, summary="Un profilo stampante")
def get_printer(preset: str) -> PrinterProfile:
    profile = PRINTER_PRESETS.get(preset)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Profilo sconosciuto: {preset}. Disponibili: {list(PRINTER_PRESETS)}",
        )
    return profile


@router.get("/options", summary="Valori ammessi per l'interfaccia")
def list_options() -> dict:
    """Enumerazioni con le etichette italiane, per popolare i menu."""
    return {
        "tipi_incastro": [
            {"valore": j.value, "etichetta_it": j.label_it} for j in JoineryType
        ],
        "formati_export": [
            {
                "valore": f.value,
                "estensione": f.extension,
                "supporta_colore": f.supports_color,
            }
            for f in ExportFormat
        ],
        "slicer": [
            {
                "valore": s.value,
                "etichetta_it": s.label_it,
                "formato_consigliato": SLICER_PROFILES[s].preferred_format.value,
                "supporta_ams": SLICER_PROFILES[s].supports_ams,
                "note_it": SLICER_PROFILES[s].notes_it,
            }
            for s in SlicerTarget
        ],
        "parti": [{"valore": p.value, "etichetta_it": p.label_it} for p in PartType],
        "passi_pipeline": [
            {"valore": s.value, "etichetta_it": s.label_it} for s in StepId
        ],
        "modalita_interfaccia": [
            {"valore": UIMode.BEGINNER.value, "etichetta_it": "Principiante"},
            {"valore": UIMode.EXPERT.value, "etichetta_it": "Esperto"},
        ],
        "classi_tolleranza": [
            {
                "nome": nome,
                "minimo_mm": minimo,
                "massimo_mm": massimo,
                "descrizione_it": descrizione,
            }
            for nome, (minimo, massimo, descrizione) in FIT_CLASSES.items()
        ],
        "controlli_stampa": [
            {
                "codice": r.code.value,
                "nome_it": r.name_it,
                "descrizione_it": r.description_it,
                "correzione_automatica": r.auto_fixable,
            }
            for r in RULE_INFO
        ],
    }


@router.get("/tolerance/{value}", summary="Classe di accoppiamento di una tolleranza")
def describe_tolerance(value: float) -> dict:
    """Spiega che tipo di accoppiamento produce una tolleranza data."""
    if not (0.05 <= value <= 0.5):
        raise HTTPException(
            status_code=422,
            detail="La tolleranza deve essere compresa fra 0,05 e 0,5 mm",
        )
    nome, descrizione = fit_class_for(value)
    return {"tolleranza_mm": value, "classe": nome, "descrizione_it": descrizione}


@router.get("/providers", summary="Stato dei provider AI")
def list_providers() -> list[dict]:
    return registry.describe_all()


@router.get("/plugins", summary="Plugin installati")
def list_plugins() -> list[dict]:
    return plugin_manager.describe()


@router.post("/plugins/reload", summary="Ricarica i plugin")
def reload_plugins() -> dict:
    """Rilegge la cartella dei plugin senza riavviare l'applicazione."""
    plugins = plugin_manager.load_all()
    attivi = sum(1 for p in plugins if p.enabled and p.error_it is None)
    return {
        "totale": len(plugins),
        "attivi": attivi,
        "messaggio_it": f"{attivi} plugin attivi su {len(plugins)} trovati",
        "plugin": plugin_manager.describe(),
    }


@router.get("/updates/check", summary="Controlla gli aggiornamenti")
async def check_updates() -> dict:
    """Interroga il server degli aggiornamenti."""
    info = await update_service.check()
    return {
        "disponibile": info.available,
        "versione_attuale": info.current_version,
        "versione_disponibile": info.latest_version,
        "data": info.release_date,
        "note_it": info.notes_it,
        "obbligatorio": info.mandatory,
        "dimensione_byte": info.size_bytes,
        "messaggio_it": info.message_it(),
        "errore_it": info.error_it,
    }


def _boolean_engine() -> str:
    """Motore booleano attualmente disponibile."""
    from ...mesh.booleans import engine_name

    return engine_name()

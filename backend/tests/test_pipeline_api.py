"""Test dell'API, della persistenza e della pipeline completa.

Il test end-to-end usa il generatore locale, quindi non richiede né rete né
chiavi API: la suite è eseguibile ovunque.
"""

from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from printready.main import create_app


@pytest.fixture(scope="module")
def client():
    """Client di test con il ciclo di vita dell'applicazione attivo."""
    with TestClient(create_app()) as test_client:
        yield test_client


def immagine_figura() -> io.BytesIO:
    """Silhouette sintetica di una figura umanoide su sfondo uniforme."""
    img = Image.new("RGB", (300, 460), (252, 252, 252))
    disegno = ImageDraw.Draw(img)
    disegno.rectangle([60, 12, 240, 62], fill=(90, 60, 40))       # cappello
    disegno.ellipse([50, 38, 250, 220], fill=(232, 196, 150))     # testa
    disegno.rectangle([105, 216, 195, 350], fill=(50, 80, 170))   # corpo
    disegno.rectangle([80, 228, 105, 330], fill=(50, 80, 170))    # braccio sinistro
    disegno.rectangle([195, 228, 220, 330], fill=(50, 80, 170))   # braccio destro
    disegno.rectangle([118, 346, 143, 420], fill=(40, 40, 45))    # gamba sinistra
    disegno.rectangle([158, 346, 183, 420], fill=(40, 40, 45))    # gamba destra
    disegno.rectangle([110, 416, 150, 440], fill=(25, 25, 25))    # scarpa sinistra
    disegno.rectangle([152, 416, 192, 440], fill=(25, 25, 25))    # scarpa destra

    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    buffer.seek(0)
    return buffer


class TestStatoEConfigurazione:
    def test_health(self, client):
        risposta = client.get("/api/health")

        assert risposta.status_code == 200
        assert risposta.json()["stato"] == "attivo"

    def test_impostazioni_non_espongono_le_chiavi(self, client):
        corpo = client.get("/api/settings").json()

        assert "provider" in corpo
        testo = risposta_testuale(corpo)
        assert "api_key" not in testo
        assert "sk-" not in testo

    def test_opzioni_hanno_le_etichette_italiane(self, client):
        opzioni = client.get("/api/settings/options").json()

        assert len(opzioni["tipi_incastro"]) == 5
        assert all(voce["etichetta_it"] for voce in opzioni["tipi_incastro"])
        assert len(opzioni["formati_export"]) == 5

    def test_profili_stampante(self, client):
        stampanti = client.get("/api/settings/printers").json()

        assert "bambu_x1c" in stampanti
        assert stampanti["bambu_x1c"]["has_ams"]

    @pytest.mark.parametrize("valore,classe", [(0.06, "forzato"), (0.25, "scorrevole")])
    def test_descrizione_tolleranza(self, client, valore, classe):
        corpo = client.get(f"/api/settings/tolerance/{valore}").json()

        assert corpo["classe"] == classe

    def test_tolleranza_fuori_range_rifiutata(self, client):
        assert client.get("/api/settings/tolerance/0.9").status_code == 422


class TestProgetti:
    def test_ciclo_di_vita(self, client):
        creato = client.post(
            "/api/projects", json={"name": "Prova", "prompt": "cavaliere"}
        ).json()
        pid = creato["id"]

        assert creato["name"] == "Prova"
        assert client.get(f"/api/projects/{pid}").json()["prompt"] == "cavaliere"

        aggiornato = client.patch(f"/api/projects/{pid}", json={"name": "Rinominato"}).json()
        assert aggiornato["name"] == "Rinominato"

        assert client.delete(f"/api/projects/{pid}").status_code == 204
        assert client.get(f"/api/projects/{pid}").status_code == 404

    def test_progetto_inesistente(self, client):
        assert client.get("/api/projects/inesistente").status_code == 404

    def test_caricamento_immagine(self, client):
        pid = client.post("/api/projects", json={"name": "Con immagine"}).json()["id"]

        risposta = client.post(
            f"/api/projects/{pid}/images",
            files={"file": ("figura.png", immagine_figura(), "image/png")},
        )

        assert risposta.status_code == 200
        assert risposta.json()["width"] == 300
        assert risposta.json()["is_primary"]

    def test_formato_immagine_non_supportato(self, client):
        pid = client.post("/api/projects", json={"name": "Formato"}).json()["id"]

        risposta = client.post(
            f"/api/projects/{pid}/images",
            files={"file": ("documento.txt", io.BytesIO(b"testo"), "text/plain")},
        )

        assert risposta.status_code == 415

    def test_undo_e_redo(self, client):
        pid = client.post("/api/projects", json={"name": "Originale"}).json()["id"]
        client.patch(f"/api/projects/{pid}", json={"name": "Modificato"})

        assert client.get(f"/api/projects/{pid}").json()["name"] == "Modificato"

        annullato = client.post(f"/api/projects/{pid}/undo").json()
        assert annullato["name"] == "Originale"

        ripetuto = client.post(f"/api/projects/{pid}/redo").json()
        assert ripetuto["name"] == "Modificato"

    def test_cronologia(self, client):
        pid = client.post("/api/projects", json={"name": "Storia"}).json()["id"]
        client.patch(f"/api/projects/{pid}", json={"notes": "prima nota"})

        cronologia = client.get(f"/api/projects/{pid}/history").json()

        assert len(cronologia) >= 2
        assert sum(1 for voce in cronologia if voce["current"]) == 1


class TestGenerazione:
    def test_rifiuta_la_generazione_senza_immagini(self, client):
        pid = client.post("/api/projects", json={"name": "Vuoto"}).json()["id"]

        risposta = client.post("/api/generate", json={"project_id": pid})

        assert risposta.status_code == 400

    @pytest.mark.slow
    def test_pipeline_completa(self, client):
        """Percorso completo: immagine → modello diviso in pezzi → file esportati."""
        pid = client.post(
            "/api/projects",
            json={"name": "End to end", "prompt": "personaggio funko con cappello e scarpe"},
        ).json()["id"]

        client.post(
            f"/api/projects/{pid}/images",
            files={"file": ("figura.png", immagine_figura(), "image/png")},
        )

        impostazioni = client.get("/api/settings/defaults").json()
        impostazioni["ai_provider"] = "local"
        impostazioni["target_height_mm"] = 100.0
        impostazioni["optimization"]["target_faces"] = 40_000
        impostazioni["export_formats"] = ["3mf", "stl", "glb"]
        client.patch(f"/api/projects/{pid}", json={"settings": impostazioni})

        avvio = client.post("/api/generate", json={"project_id": pid})
        assert avvio.status_code == 202
        job_id = avvio.json()["job_id"]

        stato = attendi_job(client, job_id, timeout_s=600)
        assert stato["state"] == "completed", stato.get("error_it")

        rapporto = client.get(f"/api/generate/{job_id}/report").json()

        # Il modello è stato diviso in pezzi stampabili...
        assert len(rapporto["parts"]) >= 3
        assert all(pezzo["watertight"] for pezzo in rapporto["parts"])
        assert all(pezzo["volume_mm3"] > 0 for pezzo in rapporto["parts"])

        # ...con incastri fra i pezzi adiacenti...
        assert len(rapporto["connectors"]) >= 1

        # ...e i file pronti per lo slicer.
        file_prodotti = client.get(f"/api/mesh/{job_id}/files").json()
        formati = {f["formato"] for f in file_prodotti}
        assert {"stl", "3mf", "glb"} <= formati

        assert client.get(f"/api/mesh/{job_id}/instructions").status_code == 200
        assert client.get(f"/api/mesh/{job_id}/preview").status_code == 200

        # Tutti i passi del workflow sono stati eseguiti.
        eseguiti = {passo["step"] for passo in rapporto["steps"]}
        assert {"ai_mesh", "auto_repair", "segmentation", "joinery", "export"} <= eseguiti


def attendi_job(client, job_id: str, timeout_s: float = 300.0) -> dict:
    """Interroga lo stato del job fino alla conclusione."""
    scadenza = time.time() + timeout_s
    while time.time() < scadenza:
        stato = client.get(f"/api/generate/{job_id}").json()
        if stato["state"] not in ("pending", "running"):
            return stato
        time.sleep(1.0)
    raise AssertionError(f"Il job non si è concluso entro {timeout_s:.0f}s")


def risposta_testuale(corpo: dict) -> str:
    """Serializza una risposta per cercarvi dentro eventuali segreti."""
    import json

    return json.dumps(corpo).lower()

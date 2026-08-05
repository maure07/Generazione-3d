"""PrintReady AI — da immagine + prompt a modello 3D pronto per la stampa FDM.

Il pacchetto è organizzato in livelli indipendenti:

* :mod:`printready.domain` — modelli, enumerazioni, bus eventi
* :mod:`printready.ai` — generatori immagine → 3D e analisi del prompt
* :mod:`printready.mesh` — riparazione, solidificazione, ottimizzazione
* :mod:`printready.segmentation` — divisione in pezzi stampabili
* :mod:`printready.joinery` — spine, sedi e incastri magnetici
* :mod:`printready.printability` — controlli di stampa e correzioni automatiche
* :mod:`printready.ams` — ottimizzazione multicolore AMS/MMU
* :mod:`printready.exporters` — STL, OBJ, 3MF, STEP, GLB
* :mod:`printready.pipeline` — orchestrazione del workflow
* :mod:`printready.projects` — persistenza, cronologia, undo/redo
* :mod:`printready.plugins` — estensioni di terze parti
* :mod:`printready.api` — API REST e WebSocket
"""

__version__ = "1.0.0"
__all__ = ["__version__"]

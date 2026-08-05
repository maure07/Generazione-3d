"""Analisi semantica del prompt: dal testo alle parti attese del modello.

Il segmentatore geometrico sa *dove* tagliare, ma non *come chiamare* i pezzi.
Questo modulo colma il divario analizzando la descrizione scritta dall'utente e
restituendo l'insieme delle parti che ci si aspetta di trovare, con un peso di
confidenza. Il lessico è bilingue (italiano e inglese) perché i prompt reali
mescolano spesso le due lingue.

Il riconoscimento è deterministico e ispezionabile — nessuna chiamata di rete —
ed è pensato per essere esteso: un plugin può registrare termini aggiuntivi con
``register_terms`` oppure sostituire l'intero analizzatore con un modello
linguistico.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from ..domain.enums import PartType

logger = logging.getLogger(__name__)


#: Lessico: parte → termini che la evocano (italiano e inglese).
LEXICON: dict[PartType, set[str]] = {
    PartType.HAIR: {
        "capelli", "capigliatura", "chioma", "ciuffo", "frangia", "treccia", "trecce",
        "coda", "codino", "crocchia", "hair", "ponytail", "braid", "bangs", "fringe",
    },
    PartType.HAT: {
        "cappello", "berretto", "cuffia", "elmo", "elmetto", "casco", "corona",
        "cilindro", "bandana", "turbante", "cappuccio", "visiera", "hat", "cap",
        "helmet", "crown", "hood", "beanie", "headgear",
    },
    PartType.BEARD: {"barba", "pizzetto", "barbetta", "beard", "goatee", "stubble"},
    PartType.MUSTACHE: {"baffi", "baffo", "mustache", "moustache", "whiskers"},
    PartType.EYES: {
        "occhi", "occhio", "sguardo", "pupille", "iride", "occhiali", "monocolo",
        "eyes", "eye", "glasses", "goggles", "eyepatch", "benda",
    },
    PartType.EYEBROWS: {"sopracciglia", "sopracciglio", "eyebrows", "brow", "brows"},
    PartType.EARS: {"orecchie", "orecchio", "ears", "ear", "auricolari"},
    PartType.HEAD: {
        "testa", "capo", "volto", "viso", "faccia", "cranio", "muso", "head", "face",
        "skull", "portrait", "ritratto",
    },
    PartType.BODY: {
        "corpo", "torso", "busto", "tronco", "petto", "schiena", "body", "torso",
        "chest", "figura", "personaggio", "character", "figure",
    },
    PartType.ARMS: {
        "braccia", "braccio", "avambraccio", "spalla", "spalle", "gomito",
        "arms", "arm", "forearm", "shoulder", "elbow",
    },
    PartType.HANDS: {
        "mani", "mano", "dita", "dito", "pugno", "palmo", "guanti", "guanto",
        "hands", "hand", "fingers", "fist", "gloves", "glove",
    },
    PartType.LEGS: {
        "gambe", "gamba", "coscia", "cosce", "ginocchio", "ginocchia", "polpaccio",
        "legs", "leg", "thigh", "knee", "calf",
    },
    PartType.SHOES: {
        "scarpe", "scarpa", "stivali", "stivale", "sandali", "sneakers", "anfibi",
        "zoccoli", "shoes", "shoe", "boots", "boot", "sneaker", "sandals", "piedi", "feet",
    },
    PartType.CLOTHES: {
        "vestiti", "vestito", "abito", "maglia", "maglietta", "camicia", "giacca",
        "pantaloni", "gonna", "tuta", "armatura", "corazza", "uniforme", "cintura",
        "clothes", "shirt", "jacket", "pants", "skirt", "dress", "armor", "armour",
        "suit", "uniform", "belt", "costume",
    },
    PartType.CAPE: {
        "mantello", "mantella", "capa", "cappa", "tabarro", "sciarpa", "cape",
        "cloak", "mantle", "scarf", "poncho",
    },
    PartType.WEAPON: {
        "arma", "armi", "spada", "spadone", "katana", "ascia", "martello", "lancia",
        "arco", "pistola", "fucile", "blaster", "pugnale", "coltello", "bastone",
        "scudo", "falce", "tridente", "weapon", "sword", "axe", "hammer", "spear",
        "bow", "gun", "rifle", "blaster", "dagger", "knife", "staff", "shield",
        "scythe", "trident",
    },
    PartType.ACCESSORY: {
        "accessorio", "accessori", "zaino", "borsa", "collana", "orecchini",
        "bracciale", "anello", "orologio", "cuffie", "microfono", "libro", "pozione",
        "accessory", "backpack", "bag", "necklace", "earrings", "bracelet", "ring",
        "watch", "headphones", "book", "potion", "amulet", "amuleto",
    },
    PartType.BASE: {
        "basetta", "base", "piedistallo", "supporto", "zoccolo", "pedana", "targhetta",
        "pedestal", "stand", "plinth", "nameplate", "diorama",
    },
    PartType.DECORATION: {
        "decorazione", "decorazioni", "ornamento", "ornamenti", "fregio", "incisione",
        "rilievo", "logo", "stemma", "simbolo", "decoration", "ornament", "emblem",
        "engraving", "pattern", "motivo",
    },
}


#: Termini che indicano che il soggetto è una figura umanoide.
HUMANOID_HINTS = {
    "personaggio", "persona", "uomo", "donna", "bambino", "bambina", "ragazzo",
    "ragazza", "guerriero", "cavaliere", "mago", "supereroe", "eroe", "eroina",
    "soldato", "pirata", "ninja", "robot", "androide", "figura", "busto",
    "character", "person", "man", "woman", "boy", "girl", "warrior", "knight",
    "wizard", "superhero", "hero", "soldier", "pirate", "robot", "android",
    "funko", "chibi", "mascotte", "mascot",
}

#: Termini che indicano un oggetto non antropomorfo (niente proporzioni umane).
OBJECT_HINTS = {
    "oggetto", "vaso", "scatola", "veicolo", "auto", "macchina", "nave", "aereo",
    "edificio", "casa", "castello", "pianta", "fiore", "animale", "cane", "gatto",
    "drago", "dinosauro", "object", "vase", "box", "vehicle", "car", "ship",
    "plane", "building", "house", "castle", "plant", "flower", "animal", "dragon",
}

#: Negazioni che escludono una parte ("senza cappello", "no hat").
NEGATIONS = {"senza", "no", "niente", "privo", "priva", "without", "none"}


@dataclass(slots=True)
class PromptAnalysis:
    """Risultato dell'analisi del prompt."""

    raw_prompt: str
    tokens: list[str] = field(default_factory=list)
    expected_parts: dict[PartType, float] = field(default_factory=dict)
    excluded_parts: set[PartType] = field(default_factory=set)
    is_humanoid: bool = True
    is_object: bool = False
    style_hints: list[str] = field(default_factory=list)

    @property
    def ordered_parts(self) -> list[PartType]:
        """Parti attese, dalla più alla meno probabile."""
        return [p for p, _ in sorted(self.expected_parts.items(), key=lambda kv: -kv[1])]

    def confidence_for(self, part: PartType) -> float:
        """Confidenza che la parte sia presente nel modello (0-1)."""
        if part in self.excluded_parts:
            return 0.0
        return self.expected_parts.get(part, 0.0)

    def summary_it(self) -> str:
        if not self.expected_parts:
            return "Nessuna parte specifica riconosciuta nel prompt"
        nomi = ", ".join(p.label_it for p in self.ordered_parts[:8])
        tipo = "figura umanoide" if self.is_humanoid else "oggetto"
        return f"Riconosciuto {tipo}; parti attese: {nomi}"


def _normalize(text: str) -> str:
    """Minuscole, senza accenti e senza punteggiatura."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s'-]", " ", text)


def tokenize(text: str) -> list[str]:
    """Divide il testo in token normalizzati."""
    return [t for t in _normalize(text).split() if t]


def analyze_prompt(prompt: str, negative_prompt: str = "") -> PromptAnalysis:
    """Estrae dal prompt le parti attese e le caratteristiche del soggetto.

    Args:
        prompt: descrizione scritta dall'utente.
        negative_prompt: elementi da escludere esplicitamente.

    Returns:
        L'analisi completa, usata dal segmentatore per nominare i pezzi.
    """
    analysis = PromptAnalysis(raw_prompt=prompt)
    tokens = tokenize(prompt)
    analysis.tokens = tokens

    if not tokens:
        # Senza prompt assumiamo una figura umanoide: è il caso d'uso dominante.
        analysis.expected_parts = {
            PartType.HEAD: 0.6,
            PartType.BODY: 0.6,
            PartType.ARMS: 0.5,
            PartType.LEGS: 0.5,
            PartType.BASE: 0.4,
        }
        return analysis

    # Indice inverso termine → parte, costruito una volta sola.
    index = _term_index()

    for position, token in enumerate(tokens):
        part = index.get(token) or index.get(_singularize(token))
        if part is None:
            continue
        # Una negazione nelle due parole precedenti esclude la parte.
        window = tokens[max(0, position - 2) : position]
        if any(w in NEGATIONS for w in window):
            analysis.excluded_parts.add(part)
            continue
        analysis.expected_parts[part] = min(1.0, analysis.expected_parts.get(part, 0.0) + 0.45)

    for token in tokenize(negative_prompt):
        part = index.get(token) or index.get(_singularize(token))
        if part is not None:
            analysis.excluded_parts.add(part)

    token_set = set(tokens)
    analysis.is_humanoid = bool(token_set & HUMANOID_HINTS) or bool(
        {PartType.HEAD, PartType.ARMS, PartType.LEGS, PartType.BODY} & set(analysis.expected_parts)
    )
    analysis.is_object = bool(token_set & OBJECT_HINTS) and not analysis.is_humanoid

    if analysis.is_humanoid:
        # Le parti anatomiche di base sono quasi sempre presenti anche se non citate.
        for part, weight in (
            (PartType.HEAD, 0.55),
            (PartType.BODY, 0.55),
            (PartType.ARMS, 0.45),
            (PartType.LEGS, 0.45),
        ):
            if part not in analysis.excluded_parts:
                analysis.expected_parts.setdefault(part, weight)

    for part in analysis.excluded_parts:
        analysis.expected_parts.pop(part, None)

    analysis.style_hints = _detect_style(token_set)
    logger.debug("Analisi prompt: %s", analysis.summary_it())
    return analysis


def _singularize(token: str) -> str:
    """Riduzione morfologica minima per italiano e inglese."""
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _detect_style(tokens: set[str]) -> list[str]:
    """Riconosce indicazioni di stile utili al dimensionamento dei pezzi."""
    styles = {
        "funko": "proporzioni Funko Pop (testa sovradimensionata)",
        "chibi": "proporzioni chibi",
        "realistico": "proporzioni realistiche",
        "realistic": "proporzioni realistiche",
        "cartoon": "stile cartoon",
        "lowpoly": "stile low poly",
        "low-poly": "stile low poly",
        "miniatura": "scala miniatura",
        "miniature": "scala miniatura",
    }
    return [description for term, description in styles.items() if term in tokens]


_TERM_INDEX_CACHE: dict[str, PartType] | None = None


def _term_index() -> dict[str, PartType]:
    """Indice inverso termine → parte (costruito una sola volta)."""
    global _TERM_INDEX_CACHE
    if _TERM_INDEX_CACHE is None:
        index: dict[str, PartType] = {}
        for part, terms in LEXICON.items():
            for term in terms:
                index.setdefault(term, part)
        _TERM_INDEX_CACHE = index
    return _TERM_INDEX_CACHE


def register_terms(part: PartType, terms: set[str]) -> None:
    """Estende il lessico a runtime (punto di aggancio per i plugin)."""
    global _TERM_INDEX_CACHE
    LEXICON.setdefault(part, set()).update(t.lower() for t in terms)
    _TERM_INDEX_CACHE = None
    logger.info("Lessico esteso per %s con %d termini", part.value, len(terms))

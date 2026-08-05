"""Compatibilità con gli slicer: profili, suggerimenti e istruzioni di montaggio.

Ogni slicer ha preferenze diverse su formati e convenzioni. Qui raccogliamo, per
ciascuno, il formato consigliato e le note operative da mostrare all'utente
insieme ai file esportati.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import ExportFormat, SlicerTarget
from ..domain.models import AMSPlan, ConnectorInfo, PartInfo


@dataclass(slots=True)
class SlicerProfile:
    """Preferenze di uno slicer."""

    target: SlicerTarget
    preferred_format: ExportFormat
    supports_ams: bool
    supports_multi_object: bool
    notes_it: str

    @property
    def label_it(self) -> str:
        return self.target.label_it


SLICER_PROFILES: dict[SlicerTarget, SlicerProfile] = {
    SlicerTarget.BAMBU_STUDIO: SlicerProfile(
        target=SlicerTarget.BAMBU_STUDIO,
        preferred_format=ExportFormat.THREEMF,
        supports_ams=True,
        supports_multi_object=True,
        notes_it=(
            "Importare il 3MF combinato: gli oggetti e i colori arrivano già separati. "
            "Assegnare gli slot AMS seguendo la legenda dei colori del rapporto."
        ),
    ),
    SlicerTarget.ORCA_SLICER: SlicerProfile(
        target=SlicerTarget.ORCA_SLICER,
        preferred_format=ExportFormat.THREEMF,
        supports_ams=True,
        supports_multi_object=True,
        notes_it=(
            "Importare il 3MF combinato. Per stampare un gruppo di colore alla volta "
            "disattivare gli altri oggetti dalla lista, evitando la torre di spurgo."
        ),
    ),
    SlicerTarget.PRUSA_SLICER: SlicerProfile(
        target=SlicerTarget.PRUSA_SLICER,
        preferred_format=ExportFormat.THREEMF,
        supports_ams=True,
        supports_multi_object=True,
        notes_it=(
            "Il 3MF conserva la suddivisione in oggetti. Con MMU assegnare le estrusioni "
            "secondo la legenda dei colori."
        ),
    ),
    SlicerTarget.CURA: SlicerProfile(
        target=SlicerTarget.CURA,
        preferred_format=ExportFormat.STL,
        supports_ams=False,
        supports_multi_object=False,
        notes_it=(
            "Cura gestisce meglio un file STL per pezzo: caricare i singoli file e "
            "posizionarli sul piatto. Il 3MF combinato resta disponibile in alternativa."
        ),
    ),
    SlicerTarget.ANYCUBIC_SLICER: SlicerProfile(
        target=SlicerTarget.ANYCUBIC_SLICER,
        preferred_format=ExportFormat.STL,
        supports_ams=False,
        supports_multi_object=False,
        notes_it=(
            "Caricare gli STL dei singoli pezzi. Verificare l'orientamento: "
            "i pezzi sono già appoggiati sulla faccia più stabile."
        ),
    ),
}


def recommended_formats(targets: list[SlicerTarget]) -> list[ExportFormat]:
    """Formati minimi da esportare per coprire tutti gli slicer indicati."""
    formats: list[ExportFormat] = []
    for target in targets:
        profile = SLICER_PROFILES.get(target)
        if profile and profile.preferred_format not in formats:
            formats.append(profile.preferred_format)
    return formats or [ExportFormat.THREEMF]


def slicer_notes_it(targets: list[SlicerTarget]) -> list[str]:
    """Note operative per gli slicer selezionati."""
    notes: list[str] = []
    for target in targets:
        profile = SLICER_PROFILES.get(target)
        if profile:
            notes.append(f"{profile.label_it}: {profile.notes_it}")
    return notes


def build_assembly_instructions(
    parts: list[PartInfo], connectors: list[ConnectorInfo], ams_plan: AMSPlan | None = None
) -> str:
    """Genera le istruzioni di montaggio in italiano, pronte da salvare in un file.

    Args:
        parts: pezzi esportati.
        connectors: incastri creati fra i pezzi.
        ams_plan: piano colori, per indicare quale filamento usare.

    Returns:
        Testo formattato in Markdown.
    """
    by_id = {p.id: p for p in parts}
    righe: list[str] = [
        "# Istruzioni di montaggio",
        "",
        "Modello preparato con PrintReady AI.",
        "",
        "## Elenco dei pezzi",
        "",
    ]

    for index, part in enumerate(parts, start=1):
        colore = ""
        if ams_plan and part.id in ams_plan.part_assignment:
            slot = ams_plan.part_assignment[part.id]
            nome_colore = ams_plan.slot_names_it.get(slot, "")
            colore = f" — filamento slot {slot + 1} ({nome_colore})"
        dimensioni = ""
        if part.bounds is not None:
            size = part.bounds.size
            dimensioni = f" — {size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm"
        righe.append(f"{index}. **{part.name}**{dimensioni}{colore}")

    if connectors:
        righe.extend(["", "## Incastri", ""])
        magneti = 0
        for connector in connectors:
            maschio = by_id.get(connector.male_part_id)
            femmina = by_id.get(connector.female_part_id)
            if maschio is None or femmina is None:
                continue
            righe.append(
                f"- **{maschio.name}** → **{femmina.name}**: {connector.description_it}"
            )
            if connector.magnet_spec:
                magneti += 1

        if magneti:
            spec = next((c.magnet_spec for c in connectors if c.magnet_spec), "")
            righe.extend(
                [
                    "",
                    f"### Materiale necessario",
                    "",
                    f"- {magneti * 2} × {spec}",
                    "",
                    "Incollare i magneti nelle sedi rispettando la polarità: "
                    "provare l'accoppiamento prima di applicare la colla.",
                ]
            )

    righe.extend(
        [
            "",
            "## Sequenza consigliata",
            "",
            "1. Rimuovere con cura eventuali supporti e sbavature dalle sedi degli incastri.",
            "2. Provare l'accoppiamento a secco di ogni coppia di pezzi.",
            "3. Se una spina entra a fatica, ridurre di poco con carta abrasiva fine; "
            "se invece balla, aumentare la tolleranza e ristampare il pezzo maschio.",
            "4. Assemblare dal basso verso l'alto: basetta, gambe, corpo, braccia, testa.",
            "5. Gli elementi decorativi vanno per ultimi.",
            "",
        ]
    )

    if ams_plan and ams_plan.notes_it:
        righe.extend(["## Note sui colori", ""])
        righe.extend(f"- {nota}" for nota in ams_plan.notes_it)
        righe.append("")

    return "\n".join(righe)

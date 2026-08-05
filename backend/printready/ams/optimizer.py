"""Ottimizzazione multicolore per sistemi AMS/MMU.

Su una stampante multimateriale a singolo ugello ogni cambio colore costa uno
spurgo (la *purge tower*): decine o centinaia di millimetri cubi di filamento
buttati, più il tempo del cambio. Su un modello con molte parti colorate lo
spreco può superare il peso del modello stesso.

Questo modulo riduce quel costo agendo su due leve:

1. **Ordine di stampa** — stampando insieme tutti i pezzi dello stesso colore,
   il numero di cambi scende dal numero di pezzi al numero di colori.
2. **Separazione fisica** — un pezzo che porta un solo colore non richiede
   alcun cambio: la segmentazione a monte è già l'ottimizzazione più efficace,
   e qui la si sfrutta appieno.

Il piano risultante indica, per ogni pezzo, quale slot AMS usare e in che
ordine stampare, con la stima del risparmio ottenuto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import trimesh

from ..domain.models import AMSPlan, AMSSettings, PrinterProfile
from ..mesh.io import is_empty
from .color_extract import ColorSlot, extract_part_colors, quantize_colors

logger = logging.getLogger(__name__)

#: Tempo medio di un cambio colore, in minuti (spurgo + ripresa).
SWAP_TIME_MIN = 0.6


@dataclass(slots=True)
class AMSOptimizationResult:
    """Esito dell'ottimizzazione AMS."""

    plan: AMSPlan
    slots: list[ColorSlot] = field(default_factory=list)

    def message_it(self) -> str:
        plan = self.plan
        if len(plan.slots) <= 1:
            return "Modello monocolore: nessuna ottimizzazione AMS necessaria"
        risparmio = plan.color_changes_naive - plan.color_changes
        if risparmio <= 0:
            return (
                f"Piano AMS su {len(plan.slots)} colori, "
                f"{plan.color_changes} cambi filamento"
            )
        return (
            f"Piano AMS su {len(plan.slots)} colori: cambi filamento ridotti da "
            f"{plan.color_changes_naive} a {plan.color_changes} "
            f"(−{plan.purge_waste_saved_mm3:.0f} mm³ di spurgo, "
            f"−{plan.estimated_time_saved_min:.0f} min)"
        )


class AMSOptimizer:
    """Costruisce il piano di stampa multicolore."""

    def __init__(
        self, settings: AMSSettings | None = None, printer: PrinterProfile | None = None
    ) -> None:
        self.settings = settings or AMSSettings()
        self.printer = printer or PrinterProfile()

    def optimize(
        self,
        parts: dict[str, tuple[str, trimesh.Trimesh]],
        declared_colors: dict[str, str] | None = None,
    ) -> AMSOptimizationResult:
        """Assegna gli slot e calcola l'ordine di stampa ottimale.

        Args:
            parts: mappa ``part_id -> (nome, mesh)``.
            declared_colors: colori già determinati dalla segmentazione.

        Returns:
            Il piano AMS con le stime di risparmio.
        """
        plan = AMSPlan()
        result = AMSOptimizationResult(plan=plan)

        if not self.settings.enabled or not parts:
            plan.notes_it.append("Ottimizzazione AMS disattivata")
            return result

        max_colors = min(self.settings.max_colors, self.printer.ams_slots)
        if not self.printer.has_ams:
            plan.notes_it.append(
                "La stampante selezionata non ha un sistema AMS/MMU: "
                "i pezzi vanno stampati separatamente con il filamento indicato"
            )

        volumes = {
            part_id: (abs(float(mesh.volume)) if not is_empty(mesh) and mesh.is_watertight else 0.0)
            for part_id, (_, mesh) in parts.items()
        }
        colors = extract_part_colors(parts, declared_colors)
        slots, assignment = quantize_colors(colors, volumes, max_colors)

        if not slots:
            plan.notes_it.append("Nessun colore rilevato nel modello")
            return result

        result.slots = slots
        plan.slots = {slot.index: slot.hex for slot in slots}
        plan.slot_names_it = {slot.index: slot.name_it for slot in slots}
        plan.part_assignment = assignment

        # Ordine ingenuo: i pezzi così come sono stati generati.
        naive_order = list(parts)
        plan.color_changes_naive = _count_changes(naive_order, assignment)

        if self.settings.minimize_swaps:
            plan.print_order = self._order_by_color(parts, assignment, volumes, slots)
        else:
            plan.print_order = naive_order
        plan.color_changes = _count_changes(plan.print_order, assignment)

        saved_swaps = max(0, plan.color_changes_naive - plan.color_changes)
        plan.purge_waste_mm3 = plan.color_changes * self.printer.purge_volume_mm3
        plan.purge_waste_saved_mm3 = saved_swaps * self.printer.purge_volume_mm3
        plan.estimated_time_saved_min = saved_swaps * SWAP_TIME_MIN

        self._add_notes(plan, slots, parts)
        logger.info("Ottimizzazione AMS: %s", result.message_it())
        return result

    # -- strategia di ordinamento -----------------------------------------

    def _order_by_color(
        self,
        parts: dict[str, tuple[str, trimesh.Trimesh]],
        assignment: dict[str, int],
        volumes: dict[str, float],
        slots: list[ColorSlot],
    ) -> list[str]:
        """Raggruppa i pezzi per slot: un solo cambio per colore.

        All'interno di ogni gruppo i pezzi sono ordinati per volume decrescente,
        così i pezzi grandi (più a rischio di distacco) partono per primi, quando
        il piatto è più libero.
        """
        by_slot: dict[int, list[str]] = {}
        for part_id in parts:
            by_slot.setdefault(assignment.get(part_id, 0), []).append(part_id)

        # I gruppi più voluminosi per primi: il colore dominante resta caricato
        # più a lungo e riduce il rischio di trafilamenti a fine bobina.
        slot_order = [slot.index for slot in sorted(slots, key=lambda s: -s.volume_mm3)]

        order: list[str] = []
        for slot_index in slot_order:
            group = by_slot.get(slot_index, [])
            group.sort(key=lambda p: -volumes.get(p, 0.0))
            order.extend(group)

        # Eventuali pezzi non assegnati chiudono la lista.
        for part_id in parts:
            if part_id not in order:
                order.append(part_id)
        return order

    def _add_notes(
        self,
        plan: AMSPlan,
        slots: list[ColorSlot],
        parts: dict[str, tuple[str, trimesh.Trimesh]],
    ) -> None:
        """Aggiunge al piano le note operative per l'utente."""
        for slot in slots:
            nomi = [parts[p][0] for p in (slot.part_ids or []) if p in parts]
            elenco = ", ".join(nomi[:5])
            if len(nomi) > 5:
                elenco += f" e altri {len(nomi) - 5}"
            plan.notes_it.append(
                f"Slot {slot.index + 1} — {slot.name_it} ({slot.hex}): {elenco}"
            )

        if plan.color_changes > self.settings.max_acceptable_swaps:
            plan.notes_it.append(
                f"Attenzione: {plan.color_changes} cambi filamento superano la soglia "
                f"di {self.settings.max_acceptable_swaps}. Valutare di stampare i "
                "gruppi di colore in lavori separati."
            )

        if self.settings.prefer_part_split_over_swap and len(slots) > 1:
            plan.notes_it.append(
                "I pezzi sono già separati per colore: stampando un gruppo alla volta "
                "la torre di spurgo non serve affatto."
            )

        plan.notes_it.append(
            f"Spurgo stimato: {plan.purge_waste_mm3:.0f} mm³ "
            f"({plan.purge_waste_mm3 / 1000 * 1.24:.1f} g di filamento)"
        )


def _count_changes(order: list[str], assignment: dict[str, int]) -> int:
    """Numero di cambi filamento in una sequenza di stampa."""
    changes = 0
    previous: int | None = None
    for part_id in order:
        slot = assignment.get(part_id)
        if slot is None:
            continue
        if previous is not None and slot != previous:
            changes += 1
        previous = slot
    return changes

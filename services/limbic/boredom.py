"""Cœur de décision pur du boredom (sans I/O) : facile à tester, aucune dépendance NATS."""

from dataclasses import dataclass, replace

# Fallback si lobe.topic.generate est indisponible/timeout (#15) — le sujet nominal
# est réfléchi par le LLM via cet appel request-reply.
PLACEHOLDER_TOPIC = "Lance une conversation sur un sujet qui te passe par la tête."


@dataclass(frozen=True)
class BoredomState:
    boredom: float = 0.0


def tick(state: BoredomState, rate: float) -> BoredomState:
    """Incrémente le boredom d'un cran. Pas de plafond : should_trigger décide seul du déclenchement."""
    return replace(state, boredom=state.boredom + rate)


def reset(state: BoredomState) -> BoredomState:
    """Remet le boredom à 0 (ex: à la réception de cortex.interaction.started)."""
    return BoredomState()


def should_trigger(boredom: float, threshold: float, presence: bool, hour: int, gate_start_hour: int, gate_end_hour: int) -> bool:
    """True si le boredom est critique et que les deux gates (présence + horaire) sont ouvertes.

    Le boredom ne décroît jamais tout seul (cf. tick) : tant qu'aucune interaction n'a eu lieu,
    il reste au-dessus du seuil une fois franchi, donc un gate fermé au moment critique n'empêche
    pas le déclenchement dès que les gates s'ouvrent (pas besoin de re-franchir le seuil).
    """
    if boredom < threshold or not presence:
        return False
    return gate_start_hour <= hour < gate_end_hour

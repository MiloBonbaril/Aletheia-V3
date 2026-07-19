"""Cœur de décision pur du mood (sans I/O) : facile à tester, aucune dépendance NATS."""

from dataclasses import dataclass, replace
from typing import Optional

NEUTRAL_EMOTION = "neutral"


@dataclass(frozen=True)
class MoodState:
    emotion: str = NEUTRAL_EMOTION
    intensity: float = 0.0
    description: Optional[str] = None

    def to_dict(self) -> dict:
        return {"emotion": self.emotion, "intensity": self.intensity, "description": self.description}


def apply_set(state: MoodState, emotion: str, intensity: float, description: Optional[str] = None) -> MoodState:
    """Applique un `limbic.mood.set` : remplace intégralement l'état courant."""
    clamped = max(0.0, min(1.0, intensity))
    return MoodState(emotion=emotion, intensity=clamped, description=description)


def decay(state: MoodState, rate: float) -> MoodState:
    """Fait décroître l'intensité vers la baseline neutre. Retourne l'objet inchangé si déjà à la baseline."""
    if state.intensity <= 0.0:
        return state

    new_intensity = max(0.0, state.intensity - rate)
    if new_intensity == 0.0:
        return MoodState()

    return replace(state, intensity=new_intensity)

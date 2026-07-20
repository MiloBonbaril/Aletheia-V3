"""Cœur pur du calcul d'occupation vocale (sans I/O), testable sans discord.py."""


def compute_occupancy(members) -> bool:
    """True si au moins un membre non-bot est présent parmi les membres donnés."""
    return any(not m.bot for m in members)

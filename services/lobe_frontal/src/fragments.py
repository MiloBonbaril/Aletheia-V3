import re

# Découpe du flux LLM en fragments publiés sur lobe.fragment_stream : un fragment
# part au premier signe de ponctuation, et c'est lui qui déclenche la voix côté
# io_voix. Vit ici (et non dans main.py) pour être importable sans réveiller
# l'interface d'inférence, le TUI et le logging fichier — le banc d'eval mesure
# le TTFF avec exactement cette segmentation, sinon il mesurerait un découpage
# que la production ne produit pas.
PUNCTUATION_PATTERN = re.compile(r'([.!?\n]+)')


def take_fragment(buffer: str) -> tuple[str | None, str]:
    """Retire du buffer le premier fragment ponctué. Retourne (fragment, reste).

    Une seule recherche par appel : un token qui contient deux ponctuations ne
    livre que la première maintenant, la suivante partira au token d'après.
    Partagé entre la boucle de génération (main.py) et le banc d'eval, pour que
    la parité ne soit pas maintenue à la main des deux côtés.
    """
    match = PUNCTUATION_PATTERN.search(buffer)
    if not match:
        return None, buffer
    return buffer[:match.end()].strip() or None, buffer[match.end():]

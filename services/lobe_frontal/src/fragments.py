import re

# Découpe du flux LLM en fragments publiés sur lobe.fragment_stream : un fragment
# part au premier signe de ponctuation, et c'est lui qui déclenche la voix côté
# io_voix. Vit ici (et non dans main.py) pour être importable sans réveiller
# l'interface d'inférence, le TUI et le logging fichier — le banc d'eval mesure
# le TTFF avec exactement ce motif, sinon il mesurerait une segmentation que la
# production ne produit pas.
PUNCTUATION_PATTERN = re.compile(r'([.!?\n]+)')

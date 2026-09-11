---
id: 3
title: Veille du modèle TTS
status: amelioration-continue
service: io_voix
priority: normale
created: 2026-09-12
updated: 2026-09-12
---

Axe de veille permanente : ce ticket ne se ferme jamais. Un nouveau modèle de synthèse sort, on
l'essaie, on note le résultat ici.

## État actuel

- Modèle : Audio8 TTS Preview 0.6B, sur GPU, poids en INT8.
- Sortie à 44,1 kHz, un fragment à la fois : les caches clés/valeurs et les graphes capturés sont
  partagés.
- Les graphes CUDA sont obligatoires. Sans eux le facteur temps réel passe de 0,22 à 2,2, et le
  service ne tient plus aucune cible.
- Le modèle est autorégressif : `engine.py` rend l'audio d'un fragment en morceaux de plus en plus
  longs, pour sortir le premier son tôt.
- Une voix de référence est obligatoire (`A8_VOICE_WAV` et `A8_VOICE_TEXT`). Sans elle, le modèle
  invente une voix différente à chaque fragment.

## Cible

- Temps jusqu'au premier son **sous 300 ms** de bout en bout. La chaîne actuelle est à 120 ms.
- Facteur temps réel bien sous 1, sinon la voix prend du retard sur elle-même.
- Un candidat doit accepter une voix de référence : un modèle à voix imposée ne convient pas.
- La mémoire GPU compte : la carte est partagée avec le LLM et le STT.

## Méthode

Mesurer avec `services/benchmark` sur un pipeline vivant. Un essai sans mesure ne vaut rien.

## Journal des essais

| Date | Modèle essayé | Mesure | Verdict |
|---|---|---|---|
| 2026-09-12 | Audio8 TTS Preview 0.6B (INT8, graphes CUDA) | 120 ms jusqu'au premier son, FTR 0,22 | retenu |

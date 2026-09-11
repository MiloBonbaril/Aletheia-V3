---
id: 1
title: Veille du modèle LLM
status: amelioration-continue
service: lobe_frontal
priority: haute
created: 2026-09-12
updated: 2026-09-12
---

Axe de veille permanente : ce ticket ne se ferme jamais. Un nouveau modèle sort, on l'essaie, on
note le résultat ici.

## État actuel

- Modèle : Gemma-4 12B it, quantification QAT `UD-Q4_K_XL`, servi par `llama-server`.
- Décodage spéculatif : modèle de brouillon MTP Gemma-4 12B, 4 jetons au plus par passe.
- Contexte de 15 000 jetons, cache clés/valeurs en `q8_0`, toutes les couches sur le GPU.
- Une seule requête à la fois (`--parallel 1`), attention flash active, projecteur multimodal chargé.

## Cible

- Temps jusqu'au premier jeton **sous 200 ms**, mesuré par `services/benchmark`.
- Les quatre outils — `save_to_memory`, `get_from_memory`, `stay_silent`, `set_mood` — appelés de
  façon fiable à la température de production, pas seulement une fois sur cinq.
- Réponse en français, sans fuite de balise XML du prompt système.
- Le modèle partage 16 Go de VRAM avec `io_voix` et `io_oreilles` : un modèle qui tient la latence
  mais prend toute la carte ne passe pas.

## Méthode

Le banc isolé `services/lobe_frontal/eval/` compare plusieurs modèles en parlant HTTP directement à
`llama-server`, hors NATS. Lancer `python eval/dump_history.py` d'abord, sinon seul le scénario
`froid` est mesuré. Lancer sur un GPU au repos.

Ne jamais mesurer une latence à la main.

## Journal des essais

| Date | Modèle essayé | Mesure | Verdict |
|---|---|---|---|
| 2026-09-12 | Gemma-4 12B QAT + brouillon MTP | référence en service | retenu |

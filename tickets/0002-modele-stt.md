---
id: 2
title: Veille du modèle STT
status: amelioration-continue
service: io_oreilles
priority: normale
created: 2026-09-12
updated: 2026-09-12
---

Axe de veille permanente : ce ticket ne se ferme jamais. Un nouveau modèle de transcription sort, on
l'essaie, on note le résultat ici.

## État actuel

- Modèle : Whisper large-v3-turbo, converti pour CTranslate2 (`model/whisper-large-turbo-ct2`).
- Whisper small, également converti, reste sur le disque comme repli rapide.
- Segmentation par Silero VAD avant la transcription.
- La compilation GPU (`cargo build --release --features cuda`) est nécessaire : sans elle, la
  transcription ne tient pas le temps réel.
- Deux sources : le microphone local, ou l'audio PCM par locuteur venu de `io_discord` avec
  `--discord`.

## Cible

- La transcription entre dans le budget de 300 ms jusqu'au premier son : tout ce qu'elle prend est
  pris au reste de la chaîne.
- Le français doit passer sans forcer la langue à la main.
- L'identité du locuteur doit survivre au passage, jusqu'au prompt du `lobe_frontal`.
- La mémoire GPU compte : la carte est partagée avec le LLM et le TTS.

## Méthode

Mesurer avec `services/benchmark` sur un pipeline vivant. Un essai sans mesure ne vaut rien.

## Journal des essais

| Date | Modèle essayé | Mesure | Verdict |
|---|---|---|---|
| 2026-09-12 | Whisper large-v3-turbo (CTranslate2) | référence en service | retenu |

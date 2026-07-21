# 💬 I/O Discord

Ce service implémente un bot Discord servant de passerelle de communication entre les utilisateurs de Discord et le noyau cognitif de Nexus-V.

## 🎯 Rôle & Responsabilités

- **Ingestion de Messages** : Écoute les messages sur les serveurs Discord et les publie sur le topic NATS `io.user.msg.text` pour qu'ils soient traités par le Cortex.
- **Diffusion des Réponses** : S'abonne au flux `lobe.fragment_stream` pour renvoyer les réponses de l'IA en temps réel sur le canal Discord.
- **Présence Vocale** : Publie `io.presence.discord_voice` à chaque changement d'occupation des salons vocaux, pour que d'autres services puissent conditionner leur comportement à la présence d'un public.
- **Capture Vocale (test)** : Commandes slash `/voice join`, `/voice leave`, `/voice record` pour rejoindre un salon vocal et enregistrer un échantillon audio (mp3) de tous les locuteurs présents, afin de vérifier la capture avant de brancher un pipeline en aval.
- **Interface Utilisateur** : Permet une interaction asynchrone avec l'entité sans nécessiter d'interface locale.

## ⚙️ Configuration & Lancement

### Dépendances
- Python 3.10+
- Bibliothèque `discord.py` (ou équivalent).
- `ffmpeg` sur le PATH (requis par `/voice record` pour encoder l'audio capturé en mp3).

### Variables d'Environnement
- `DISCORD_TOKEN` : Jeton d'authentification du Bot Discord.
- `NATS_URL` : URL du broker NATS.

### Lancement
```bash
python bot.py
```

## 🔌 Interface NATS
- **Publie sur** : `io.user.msg.text`, `io.presence.discord_voice`
- **S'abonne à** : `lobe.fragment_stream`

## 🧪 Tests
```bash
pytest tests/
```

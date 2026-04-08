# I/O Yeux (Twitch)

Le senseur chargé d'observer le monde virtuel.

### Rôle principal :
- Se connecter à l'API de chat Twitch ou YouTube.
- Agréger les messages et événements (sub, bits, etc.) pour éviter l'inondation en période de forte affluence.
- Engendrer des événements synthétiques de type `CHAT_SUMMARY` vers l'Orchestrateur.

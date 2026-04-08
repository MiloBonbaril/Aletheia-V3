# I/O Visage (VTube Controller)

L'actionneur chargé de contrôler l'avatar en direct.

### Rôle principal :
- Capter l'audio sortant du TTS pour générer un Lip-Sync.
- Récupérer les tags émotionnels insérés par le LLM (ex: `<joy>`).
- Envoyer les ordres et triggers via WebSocket à VTube Studio pour afficher les expressions en synchronisation temporelle.

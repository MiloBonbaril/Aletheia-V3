# I/O Discord

Un service maintenant un bot discord permettant de communiquer avec Aletheia.

### Fonctionnalités :
- Bot discord.
- Publie les messages structurés sur le topic NATS `io.user.msg.text`.
- Envoie les réponses de Aletheia présentes sur le topic NATS `lobe.fragment_stream` directement sur discord.

### Utilisation :
1. Lancez le service : `python bot.py`
2. attendez que le bot soit connecté à discord.
3. communiquer avec Aletheia sur discord comme un autre utilisateur lambda.
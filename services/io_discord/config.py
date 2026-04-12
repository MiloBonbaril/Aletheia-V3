from dotenv import load_dotenv
import datetime
import os

# Chargement des variables d'environnement depuis le fichier .env
load_dotenv()

class Config:
    # Token du bot et ID d'utilisateur pour les notifications
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")  # à configurer dans .env
    USER_ID = int(os.getenv("DISCORD_USER_ID"))  # à configurer dans .env

    # Serveur cible pour écouter les messages
    GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))  # à configurer dans .env

    # Préfixe des commandes
    COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

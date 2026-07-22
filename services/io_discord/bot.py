# bot.py
# Discord bot for interacting with Ollama models via FastAPI (discord.py v2)
import discord
from discord.ext import commands
import os
import logging
import typing
import asyncio
import socket

import aiohttp

# add workspace to sys.path
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from config import Config

# Remplacement des variables dans bot.py par celles de Config
TOKEN = Config.DISCORD_TOKEN
USER_ID = Config.USER_ID

# Liste centrale des cogs à gérer
COGS = ["bets", "text", "presence", "voice"] # "special_message"

logger_voice = logging.getLogger('discord.voice_client')
logger_voice.setLevel(logging.DEBUG)
handler_voice = logging.FileHandler(filename='discord_voice.log', encoding='utf-8', mode='w')
handler_voice.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger_voice.addHandler(handler_voice)

logger = logging.getLogger('discord')
logger.setLevel(logging.INFO)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# Initialisation du bot
intents = discord.Intents.all()
intents.messages = True  # Nécessaire pour lire les messages
intents.message_content = True  # Requis pour lire le contenu des messages (prefix cmds)
intents.voice_states = True  # Requis pour rejoindre/déplacer en vocal

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# Discord's voice media relays (unlike the Cloudflare-fronted API/gateway) can have
# broken IPv6 routing on some networks even when IPv6 works fine everywhere else —
# force IPv4 for all of the bot's traffic (REST, gateway, and voice websocket all
# share this connector) to sidestep that.
connector = aiohttp.TCPConnector(family=socket.AF_INET, loop=loop)
bot = commands.Bot(
    command_prefix=Config.COMMAND_PREFIX,
    intents=intents,
    loop=loop,
    connector=connector,
)

async def load_extensions_and_sync():
    # Chargement des cogs et synchronisation des commandes (slash/hybrides)
    user = None
    try:
        user = await bot.fetch_user(USER_ID)
    except Exception:
        pass

    loaded_cogs = []
    for cog in COGS:
        try:
            bot.load_extension(f"cogs.{cog}")
            print(f"Cog {cog} loaded successfully.")
            loaded_cogs.append(cog)
        except Exception as e:
            print(f"Failed to load cog {cog}: {e}")

    # Ensure slash commands from all loaded cogs are registered in Discord
    try:
        await bot.sync_commands()
        print("Slash commands synced.")
        if user:
            try:
                cog_messages = ", ".join(loaded_cogs) if loaded_cogs else "no cogs"
                await user.send(f"{cog_messages} are ready")
            except Exception:
                pass
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

@bot.event
async def on_ready():
    # DM l'utilisateur défini pour signaler que le bot est prêt
    try:
        user = await bot.fetch_user(USER_ID)
        await load_extensions_and_sync() # Déjà appelé dans setup_hook
        await user.send("Bot is ready")
    except Exception:
        pass


# Depuis discord.py v2, il est recommandé de charger les extensions et
# synchroniser la command tree dans setup_hook
async def setup_hook():
    await load_extensions_and_sync()

# Attache la méthode setup_hook au bot
# bot.setup_hook = setup_hook

# Commande slash pour recharger tous les cogs et resynchroniser les commandes
@bot.slash_command(guild_ids=[Config.GUILD_ID], name="reloadcogs", description="Reload all cogs and resync commands")
@commands.has_permissions(administrator=True)
async def reload_cogs(ctx: discord.ApplicationContext):
    notes = []
    for cog in COGS:
        module = f"cogs.{cog}"
        try:
            bot.reload_extension(module)
            notes.append(f"reloaded {cog}")
        except discord.ExtensionNotLoaded:
            try:
                bot.load_extension(module)
                notes.append(f"loaded {cog}")
            except Exception as e:
                notes.append(f"failed {cog}: {e}")
        except Exception as e:
            notes.append(f"failed {cog}: {e}")

    # Re-sync slash commands so changes become visible immediately
    try:
        await bot.sync_commands()
        notes.append("synced")
    except Exception as e:
        notes.append(f"sync failed: {e}")

    await ctx.respond("Cogs reload complete: "+" | ".join(notes))

# Commande slash pour recharger un cog précis
@bot.slash_command(guild_ids=[Config.GUILD_ID], name="reloadcog", description="Reload a specific cog and resync commands")
@commands.has_permissions(administrator=True)
async def reload_cog(ctx: discord.ApplicationContext, cog_name: str):
    if cog_name not in COGS:
        await ctx.respond(f"Cog {cog_name} is not recognized.")
        return

    module = f"cogs.{cog_name}"
    try:
        bot.reload_extension(module)
        await ctx.respond(f"Cog {cog_name} reloaded successfully.")
    except discord.ExtensionNotLoaded:
        try:
            bot.load_extension(module)
            await ctx.respond(f"Cog {cog_name} loaded successfully.")
        except Exception as e:
            await ctx.respond(f"Failed to load cog {cog_name}: {e}")
    except Exception as e:
        await ctx.respond(f"Failed to reload cog {cog_name}: {e}")
        return

    # Re-sync slash commands for the guild
    try:
        await bot.sync_commands()
    except Exception as e:
        await ctx.respond(f"Warning: commands sync failed: {e}")

class MyHelp(commands.HelpCommand):
    def get_command_signature(self, command):
        return '%s%s %s' % (self.context.clean_prefix, command.qualified_name, command.signature)

    async def send_error_message(self, error):
        embed = discord.Embed(title="Error", description=error, color=discord.Color.red())
        channel = self.get_destination()

        await channel.send(embed=embed)

    async def send_group_help(self, group):
        embed = discord.Embed(title=self.get_command_signature(group), description=group.help, color=discord.Color.blurple())

        if filtered_commands := await self.filter_commands(group.commands):
            for command in filtered_commands:
                embed.add_field(name=self.get_command_signature(command), value=command.help or "No Help Message Found... ")

        await self.get_destination().send(embed=embed)

    async def send_cog_help(self, cog):
        embed = discord.Embed(title=cog.qualified_name or "No Category", description=cog.description, color=discord.Color.blurple())

        if filtered_commands := await self.filter_commands(cog.get_commands()):
            for command in filtered_commands:
                embed.add_field(name=self.get_command_signature(command), value=command.help or "No Help Message Found... ")

        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command):
        embed = discord.Embed(title=self.get_command_signature(command), color=discord.Color.random())
        if command.help:
            embed.description = command.help
        if alias := command.aliases:
            embed.add_field(name="Aliases", value=", ".join(alias), inline=False)

        channel = self.get_destination()
        await channel.send(embed=embed)

    async def send_bot_help(self, mapping):
        embed = discord.Embed(title="Help", color=discord.Color.blurple())

        for cog, commands in mapping.items():
           filtered = await self.filter_commands(commands, sort=True)
           command_signatures = [self.get_command_signature(c) for c in filtered]

           if command_signatures:
                cog_name = getattr(cog, "qualified_name", "No Category")
                embed.add_field(name=cog_name, value="\n".join(command_signatures), inline=False)

        channel = self.get_destination()
        await channel.send(embed=embed)

bot.help_command = MyHelp()

# Point d'entrée du bot
if __name__ == "__main__":
    bot.run(TOKEN)

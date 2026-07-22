# bot.py
# Discord bot gateway for Aletheia (discord.py v2 + discord-ext-voice-recv)
import discord
from discord import app_commands
from discord.ext import commands
import os
import logging

# add workspace to sys.path
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from config import Config

TOKEN = Config.DISCORD_TOKEN
USER_ID = Config.USER_ID

# Liste centrale des cogs à gérer
COGS = ["bets", "text", "presence", "voice"]  # "special_message"

# Voice diagnostics log — this file is how the 4017/DAVE failure was diagnosed;
# discord.py splits voice logging across several loggers, capture them all.
handler_voice = logging.FileHandler(filename='discord_voice.log', encoding='utf-8', mode='w')
handler_voice.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
for _name in ('discord.voice_state', 'discord.voice_client', 'discord.ext.voice_recv'):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.DEBUG)
    _lg.addHandler(handler_voice)

logger = logging.getLogger('discord')
logger.setLevel(logging.INFO)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# Initialisation du bot
intents = discord.Intents.all()

GUILD = discord.Object(id=Config.GUILD_ID)


class AletheiaBot(commands.Bot):
    async def setup_hook(self) -> None:
        # Chargement des cogs et synchronisation des commandes slash (guild-scoped)
        for cog in COGS:
            try:
                await self.load_extension(f"cogs.{cog}")
                print(f"Cog {cog} loaded successfully.")
            except Exception as e:
                print(f"Failed to load cog {cog}: {e}")
        try:
            await self.tree.sync(guild=GUILD)
            print("Slash commands synced.")
        except Exception as e:
            print(f"Failed to sync slash commands: {e}")


bot = AletheiaBot(command_prefix=Config.COMMAND_PREFIX, intents=intents)


@bot.event
async def on_ready():
    # DM l'utilisateur défini pour signaler que le bot est prêt
    try:
        user = await bot.fetch_user(USER_ID)
        await user.send("Bot is ready")
    except Exception:
        pass


# Commande slash pour recharger tous les cogs et resynchroniser les commandes
@bot.tree.command(name="reloadcogs", description="Reload all cogs and resync commands", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
async def reload_cogs(interaction: discord.Interaction):
    await interaction.response.defer()
    notes = []
    for cog in COGS:
        module = f"cogs.{cog}"
        try:
            await bot.reload_extension(module)
            notes.append(f"reloaded {cog}")
        except commands.ExtensionNotLoaded:
            try:
                await bot.load_extension(module)
                notes.append(f"loaded {cog}")
            except Exception as e:
                notes.append(f"failed {cog}: {e}")
        except Exception as e:
            notes.append(f"failed {cog}: {e}")

    # Re-sync slash commands so changes become visible immediately
    try:
        await bot.tree.sync(guild=GUILD)
        notes.append("synced")
    except Exception as e:
        notes.append(f"sync failed: {e}")

    await interaction.followup.send("Cogs reload complete: " + " | ".join(notes))


# Commande slash pour recharger un cog précis
@bot.tree.command(name="reloadcog", description="Reload a specific cog and resync commands", guild=GUILD)
@app_commands.checks.has_permissions(administrator=True)
async def reload_cog(interaction: discord.Interaction, cog_name: str):
    if cog_name not in COGS:
        await interaction.response.send_message(f"Cog {cog_name} is not recognized.")
        return

    await interaction.response.defer()
    module = f"cogs.{cog_name}"
    try:
        await bot.reload_extension(module)
    except commands.ExtensionNotLoaded:
        try:
            await bot.load_extension(module)
        except Exception as e:
            await interaction.followup.send(f"Failed to load cog {cog_name}: {e}")
            return
    except Exception as e:
        await interaction.followup.send(f"Failed to reload cog {cog_name}: {e}")
        return

    # Re-sync slash commands for the guild
    try:
        await bot.tree.sync(guild=GUILD)
    except Exception as e:
        await interaction.followup.send(f"Warning: commands sync failed: {e}")
        return
    await interaction.followup.send(f"Cog {cog_name} reloaded successfully.")


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

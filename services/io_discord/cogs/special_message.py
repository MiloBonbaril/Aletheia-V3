from discord.ext import commands
import discord
from config import Config
import random
import logging

class special_message(commands.Cog):
    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    @commands.Cog.listener("on_message")
    async def special_messages(self, msg: discord.Message) -> None:
        # Ignore messages by the bot
        if msg.author == self.bot.user:
            return
        # Ignore messages in DMs
        if msg.guild is None:
            return
        # Ignore messages in other guilds
        if msg.guild.id != Config.GUILD_ID:
            return
        # Ignore messages from bots
        if msg.author.bot:
            return
        # Ignore commands
        if msg.content.startswith(Config.COMMAND_PREFIX):
            return
        # Ignore messages that are only mentions
        if msg.mentions:
            return

        content = msg.content.lower()
        content_without_ponctuation = ''.join(e for e in content if e.isalnum())
        content_without_ponctuation_and_spaces = content_without_ponctuation.replace(" ", "")
        if content_without_ponctuation_and_spaces.endswith("quoi") or content_without_ponctuation_and_spaces.endswith("koi") or content_without_ponctuation_and_spaces.endswith("qoi") or content_without_ponctuation_and_spaces.endswith("qoa") or content_without_ponctuation_and_spaces.endswith("koa") or content_without_ponctuation_and_spaces.endswith("quoa"):
            random.seed(msg.id + msg.created_at.timestamp())
            random_response = ["feur", "https://tenor.com/view/theobabac-feur-meme-theobabac-feur-gif-11339780952727019434", "https://tenor.com/view/feur-meme-gif-24407942", "https://tenor.com/view/quoicoubeh-quoicoube-tiktok-quoi-ok-quoicoubeh-gif-27667316", "coubeh"]
            await msg.channel.send(random.choice(random_response))
            self.logger.info(f"special message detected (feur): {msg.content}")
        if content_without_ponctuation_and_spaces.startswith("hey") or content_without_ponctuation_and_spaces.startswith("salut") or content_without_ponctuation_and_spaces.startswith("yo") or content_without_ponctuation_and_spaces.startswith("bonjour") or content_without_ponctuation_and_spaces.startswith("hello"):
            # get a specific emoji and check if available:
            emoji = discord.utils.get(self.bot.emojis, name="heyyy")
            if emoji:
                await msg.add_reaction(emoji)
            else:
                await msg.add_reaction("👋")
            self.logger.info(f"special message detected (hey): {msg.content}")
        if "ratio" in content_without_ponctuation_and_spaces:
            await msg.add_reaction("👍")
            self.logger.info(f"special message detected (ratio): {msg.content}")

        # Ne pas bloquer le traitement des commandes préfixées
        try:
            await self.bot.process_commands(msg)
        except Exception:
            pass

def setup(bot):
    bot.add_cog(special_message(bot))
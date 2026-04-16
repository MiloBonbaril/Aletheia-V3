import logging
import sys
import json
import asyncio
from typing import Optional

import nats
import discord
from discord.ext import commands

from config import Config



class Text(commands.Cog):
    # commands group
    text = discord.SlashCommandGroup(
        "text",
        "Commands for the text cog",
        guild_ids=[Config.GUILD_ID],
    )
    
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("Text")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
        )
        self.logger.addHandler(handler)

        self.logger.info("Text cog initialized.")

        self.chat_activated = False
        self.nc = None
        self.active_channel = None

        # Initiate NATS
        self.bot.loop.create_task(self.setup_nats())

    async def setup_nats(self):
        try:
            self.nc = await nats.connect("nats://localhost:4222")
            self.logger.info("Connected to NATS.")
            await self.nc.subscribe("lobe.fragment_stream", cb=self.on_fragment)
        except Exception as e:
            self.logger.error(f"Failed to connect to NATS: {e}")

    async def on_fragment(self, msg) -> None:
        if not self.chat_activated or not self.active_channel:
            return
            
        data = json.loads(msg.data.decode())
        text_fragment = data.get("text", "")
        
        if text_fragment:
            try:
                await self.active_channel.send(text_fragment)
            except Exception as e:
                self.logger.error(f"Error sending fragment to Discord: {e}")

    def cleanup(self) -> None:
        self.logger.info("Cleaning up Text cog resources.")
        if self.nc and not self.nc.is_closed:
            self.bot.loop.create_task(self.nc.close())
        self.logger.handlers.clear()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.chat_activated:
            return

        if not message.guild or message.guild.id != Config.GUILD_ID:
            return

        if message.author.bot or message.author == self.bot.user:
            return

        if not message.content and not message.attachments:
            return

        self.active_channel = message.channel

        if self.nc:
            formatted_msg = f"{message.author.display_name} said: {message.content}"
            payload = {"text": formatted_msg}
            
            images = []
            for att in message.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    images.append(att.url)
                    if len(images) == 5:
                        break
                        
            if images:
                payload["images"] = images

            try:
                await self.nc.publish("io.user.msg.text", json.dumps(payload).encode())
            except Exception as e:
                self.logger.error(f"Failed to publish to NATS: {e}")

    @text.command(
        guild_ids=[Config.GUILD_ID],
        name="activate_chat",
        description="Activate the ability to chat with the assistant",
    )
    async def text_chat(
        self, ctx: discord.ApplicationContext, force_state: Optional[bool] = None
    ) -> None:
        """
        Toggle chat mode for Texte, optionally forcing the state.
        """
        self.logger.debug(
            "received the activate_chat command with force_state = %s", force_state
        )
        if force_state is None:
            self.chat_activated = not self.chat_activated
        else:
            self.chat_activated = force_state
        state = "on" if self.chat_activated else "off"
        await ctx.respond(f"chat mode set to: {state}")


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Text(bot))

def teardown(bot: commands.Bot) -> None:
    cog = bot.get_cog("Text")
    if cog:
        cog.cleanup()
    try:
        bot.remove_cog("Text")
    except Exception as exc:
        print(f"Error removing cog Text: {exc}")

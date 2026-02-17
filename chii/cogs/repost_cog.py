import json
import re
import logging

import discord
import discord.ext.commands

from chii.config import Config
from chii.utils import SimpleUtils

class RepostCog(discord.ext.commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = discord.app_commands.Group(name="repost", description="Reposting commands.")

    def __init__(self, bot: discord.ext.commands.Bot) -> None:
        self.bot = bot
        self.url_regex = re.compile(pattern=Config.REPOSTS_URL_REGEX, flags=re.IGNORECASE)

        self.l.info("RepostCog initialized.")

    def _load_data(self) -> SimpleUtils.JSON:
        default_data = {
            "channel_ids": [],
        }

        if not Config.REPOSTS_DATA_PATH.exists():
            SimpleUtils.save_data(Config.REPOSTS_DATA_PATH, default_data)
            return default_data.copy()

        with open(Config.REPOSTS_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "channel_ids" not in data:
            data["channel_ids"] = []

        return data

    @discord.ext.commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        data = self._load_data()
        channel_ids = data.get("channel_ids", [])

        if message.channel.id not in channel_ids:
            return

        match = self.url_regex.search(message.content)

        if not match:
            return

        self.l.info(f"Detected repost URL in channel {message.channel.id} by user {message.author.id}.")

        await self.bot.video_worker.enqueue({ # type: ignore
            "message": message,
            "url": match.group(1),
        })

    @group.command(name="add", description="Add a channel to the watching list.")
    @discord.app_commands.describe(channel="The channel to watch for reposts.")
    @discord.ext.commands.is_owner()
    async def repost_add(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        data = self._load_data()

        if channel.id in data["channel_ids"]:
            await interaction.response.send_message("Channel is already being watched.", ephemeral=True)
            return

        data["channel_ids"].append(channel.id)
        SimpleUtils.save_data(Config.REPOSTS_DATA_PATH, data)

        await interaction.response.send_message(f"Added {channel.mention} as repost channel.", ephemeral=True)

    @group.command(name="remove", description="Remove a channel from the watching list.")
    @discord.app_commands.describe(channel="The channel to stop watching.")
    @discord.ext.commands.is_owner()
    async def repost_remove(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        data = self._load_data()

        if channel.id not in data["channel_ids"]:
            await interaction.response.send_message("Channel not watched.", ephemeral=True)
            return

        data["channel_ids"].remove(channel.id)
        SimpleUtils.save_data(Config.REPOSTS_DATA_PATH, data)

        await interaction.response.send_message(f"Removed {channel.mention} from the watching list.", ephemeral=True)

    @group.command(name="list", description="List the channels that are currently being watched.")
    @discord.ext.commands.is_owner()
    async def repost_list(self, interaction: discord.Interaction) -> None:
        data = self._load_data()
        channel_ids = data["channel_ids"]

        if not channel_ids:
            await interaction.response.send_message("No watched channels.", ephemeral=True)
            return

        output = []

        for c_id in channel_ids:
            channel = interaction.guild.get_channel(c_id) if interaction.guild else None
            output.append(channel.mention if channel else f"`{c_id}`")

        message = (
            "Channels that are **currently** being watched:\n"
            + "\n".join(f"- {channel}" for channel in output)
        )

        await interaction.response.send_message(message, ephemeral=True)

async def setup(bot: discord.ext.commands.Bot) -> None:
    await bot.add_cog(RepostCog(bot))

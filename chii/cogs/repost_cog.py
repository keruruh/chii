import json
import logging
import pathlib
import re

from discord import Interaction, Message, TextChannel, app_commands
from discord.ext import commands

from chii.config import Config
from chii.main import video_worker
from chii.utils import T_DATA, SimpleUtils


class RepostCog(commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = app_commands.Group(name="repost", description="Reposting commands.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.url_regex = re.compile(pattern=Config.REPOSTS_URL_REGEX, flags=re.IGNORECASE)

        self.l.info("RepostCog initialized.")

    def _load_data(self) -> T_DATA:
        default_data = {
            "channel_ids": [],
        }

        if not Config.REPOSTS_DATA_PATH.exists():
            self.l.info(f"Reposts data file not found at {Config.REPOSTS_DATA_PATH}. Creating new data file...")
            SimpleUtils.save_data(Config.REPOSTS_DATA_PATH, default_data)

            return default_data.copy()

        self.l.debug(f"Loading reposts data from {Config.REPOSTS_DATA_PATH}...")

        with pathlib.Path(Config.REPOSTS_DATA_PATH).open(encoding="utf-8") as f:
            data = json.load(f)

        if "channel_ids" not in data:
            self.l.warning('The "channel_ids" key missing in reposts data! Initializing as empty list...')
            data["channel_ids"] = []

        return data

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        if message.author.bot:
            return

        match = self.url_regex.search(message.content)

        if not match:
            return

        data = self._load_data()
        channel_ids = data.get("channel_ids", [])

        if message.channel.id not in channel_ids:
            return

        self.l.info(f"Detected repost URL in channel {message.channel.id} by user {message.author.id}.")

        await video_worker.enqueue({
            "message": message,
            "url": match.group(1),
        })

        self.l.info(f"Enqueued video repost task for message {message.id}.")

    @group.command(name="add", description="Start monitoring a channel for reposting videos.")
    @commands.is_owner()
    @app_commands.describe(channel="Channel the bot should watch for videos.")
    async def repost_add(self, interaction: Interaction, channel: TextChannel) -> None:
        self.l.info(f"Received repost add command for channel {channel.id}.")

        data = self._load_data()

        if channel.id in data["channel_ids"]:
            self.l.info(f"Channel {channel.id} is already being watched for reposts.")
            await interaction.response.send_message("Channel is already being watched.", ephemeral=True)
            return

        data["channel_ids"].append(channel.id)

        SimpleUtils.save_data(Config.REPOSTS_DATA_PATH, data)
        self.l.info(f"Channel {channel.id} added to repost watch list and data saved.")

        await interaction.response.send_message(f"Added {channel.mention} as repost channel.", ephemeral=True)

    @group.command(name="remove", description="Stop monitoring a channel for reposts.")
    @commands.is_owner()
    @app_commands.describe(channel="Channel to remove from monitoring.")
    async def repost_remove(self, interaction: Interaction, channel: TextChannel) -> None:
        self.l.info(f"Received repost remove command for channel {channel.id}.")

        data = self._load_data()

        if channel.id not in data["channel_ids"]:
            self.l.info(f"Channel {channel.id} is not currently being watched for reposts.")
            await interaction.response.send_message("Channel not watched.", ephemeral=True)
            return

        data["channel_ids"].remove(channel.id)

        SimpleUtils.save_data(Config.REPOSTS_DATA_PATH, data)
        self.l.info(f"Channel {channel.id} removed from repost watch list and data saved.")

        await interaction.response.send_message(f"Removed {channel.mention} from the watching list.", ephemeral=True)

    @group.command(name="list", description="Show all channels that are currently being monitored for videos.")
    @commands.is_owner()
    async def repost_list(self, interaction: Interaction) -> None:
        self.l.info("Received repost list command.")

        data = self._load_data()
        channel_ids = data["channel_ids"]

        if not channel_ids:
            self.l.info("No channels are currently being watched for reposts.")
            await interaction.response.send_message("No watched channels.", ephemeral=True)
            return

        output = []

        for c_id in channel_ids:
            channel = interaction.guild.get_channel(c_id) if interaction.guild else None
            output.append(channel.mention if channel else f"`{c_id}`")

        message = "Channels that are **currently** being watched:\n" + "\n".join(f"- {channel}" for channel in output)

        self.l.debug(f"Listing {len(channel_ids)} watched channels.")
        await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RepostCog(bot))

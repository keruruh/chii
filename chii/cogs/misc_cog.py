import logging
import typing as t

from discord import Activity, ActivityType, Game, Interaction, app_commands
from discord.ext import commands

from chii.config import Config
from chii.utils import DumpViewer, SimpleUtils


class MiscCog(commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = app_commands.Group(name="misc", description="Miscellaneous utility commands.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self.l.info("MiscCog initialized.")

    @group.command(name="dump", description="Dump a data file with scrolling pagination.")
    @commands.is_owner()
    @app_commands.describe(filename="File inside the data path.", reverse="Read file from bottom (useful for logs).")
    async def dump_file(self: t.Self, interaction: Interaction, filename: str, reverse: bool = False) -> None:
        self.l.info(f"{interaction.user} requested dump of {filename}")

        safe_path = (Config.DATA_PATH / filename).resolve()

        if not str(safe_path).startswith(str(Config.DATA_PATH.resolve())):
            await interaction.response.send_message("Invalid file path.", ephemeral=True)
            return

        if not safe_path.exists():
            await interaction.response.send_message("File not found.", ephemeral=True)
            return

        try:
            with safe_path.open(encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            if reverse:
                lines.reverse()

            text = "".join(lines)

        except Exception:
            self.l.exception("Failed reading dump file.")
            await interaction.response.send_message("Failed to read file. See logs for more info.", ephemeral=True)
            return

        pages = SimpleUtils.paginate_text(text)

        if not pages:
            await interaction.response.send_message("File is empty.", ephemeral=True)
            return

        view = DumpViewer(file_path=safe_path, pages=pages, owner_id=interaction.user.id)

        await interaction.response.send_message(content=view.get_content(), view=view)

    @group.command(name="status", description="Change the bot status")
    @app_commands.describe(activity_type="Type of activity (playing, watching, listening, streaming).", text="Status.")
    async def change_status(self: t.Self, interaction: Interaction, activity_type: str, text: str) -> None:
        activity_type = activity_type.lower()

        match activity_type:
            case "playing":
                activity = Activity(type=ActivityType.playing, name=text)
            case "watching":
                activity = Activity(type=ActivityType.playing, name=text)
            case "listening":
                activity = Activity(type=ActivityType.listening, name=text)
            case "streaming":
                activity = Activity(type=ActivityType.streaming, name=text, url="https://twitch.tv/motxi")
            case _:
                await interaction.response.send_message("Invalid activity type.", ephemeral=True)
                return

        await self.bot.change_presence(activity=activity)
        await interaction.response.send_message(f'Status changed to: "{activity_type.title()}" **{text}**.')

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MiscCog(bot))

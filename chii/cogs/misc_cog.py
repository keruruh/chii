import logging

from discord import app_commands
from discord.ext import commands


class MiscCog(commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = app_commands.Group(name="misc", description="Miscellaneous utility commands.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self.l.info("MiscCog initialized.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MiscCog(bot))

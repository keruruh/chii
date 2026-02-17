import logging

import discord
import discord.ext.commands

class MiscCog(discord.ext.commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = discord.app_commands.Group(name="misc", description="Miscellaneous commands.")

    def __init__(self, bot: discord.ext.commands.Bot) -> None:
        self.bot = bot

        self.l.info("MiscCog initialized.")

async def setup(bot: discord.ext.commands.Bot) -> None:
    await bot.add_cog(MiscCog(bot))

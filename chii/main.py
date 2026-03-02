import logging
import pathlib

from discord import Intents
from discord.ext import commands

from chii.config import Config
from chii.utils import LogHandler, VideoWorker

LogHandler.setup()

log = logging.getLogger(__name__)

intents = Intents.default()
intents.message_content = True

bot = commands.Bot(owner_id=Config.BOT_OWNER, command_prefix=Config.BOT_PREFIX, intents=intents)
video_worker = VideoWorker(bot=bot, worker_count=3, max_queue_size=5)


@bot.event
async def on_ready() -> None:
    if bot.user:
        log.info(f"Logged in as {bot.user} with {bot.user.id} (ID).")
    else:
        log.error("Could not get bot user.")

    await bot.tree.sync()
    log.info("Synced application commands.")


async def load_cogs() -> None:
    for file in pathlib.Path("chii/cogs").rglob("*.py"):
        if file.name == "__init__.py":
            continue

        await bot.load_extension(f"chii.cogs.{file.stem}")
        log.info(f'Loaded cog: "{file.name}".')


async def start() -> None:
    log.info("Starting bot main loop...")

    async with bot:
        await load_cogs()
        log.info("Cogs loaded.")

        video_worker.start()

        await bot.start(Config.BOT_TOKEN, reconnect=True)
        log.info("Bot started.")

import asyncio
import json
import logging
import pathlib
import time
import typing as t
import uuid

from discord import Interaction, app_commands
from discord.ext import commands

from chii.config import Config
from chii.utils import T_DATA, T_NUMERIC, SimpleUtils


class ReminderCog(commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = app_commands.Group(name="reminder", description="Manage personal reminders and scheduled notifications.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.reminders = {}
        self.tasks = {}

        self.l.info("Starting ReminderCog scheduler initialization...")
        self.bot.loop.create_task(self._initialize_scheduler())
        self.l.info("ReminderCog initialized.")

    def _load_data(self) -> None:
        if not Config.REMINDERS_DATA_PATH.exists():
            self.l.info(f"Reminders data file not found at {Config.REMINDERS_DATA_PATH}. Creating new data file...")
            SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, [])
            return

        self.l.debug(f'Loading reminders from "{Config.REMINDERS_DATA_PATH}"...')

        try:
            with pathlib.Path(Config.REMINDERS_DATA_PATH).open(encoding="utf-8") as f:
                data = json.load(f)

            for r in data:
                self.reminders[r["id"]] = r

            self.l.info(f"Loaded {len(self.reminders)} reminders from disk.")

        except Exception:
            self.l.exception("Failed loading reminders!")

    async def cog_unload(self) -> None:
        self.l.info("Unloading ReminderCog and cancelling all reminder tasks...")

        for task in self.tasks.values():
            task.cancel()

        self.tasks.clear()
        self.l.info("All reminder tasks have been cancelled.")

    @group.command(name="set", description="Create a new reminder that will notify you after a specified time.")
    @app_commands.describe(time_input="The time after which the reminder should trigger (e.g.: 10s, 5m, 1h, 3d).", message="Optional custom message.")
    async def reminder_set(self: t.Self, interaction: Interaction, time_input: str, message: str | None) -> None:
        self.l.info(f"Received reminder set command from user {interaction.user.id}.")

        try:
            seconds = SimpleUtils.parse_time(time_input)
        except Exception:
            self.l.exception("Invalid time format provided for reminder.")
            await interaction.response.send_message("Invalid time format.", ephemeral=True)
            return

        if seconds < Config.REMINDERS_MIN_TIME_SEC:
            self.l.warning("Reminder time below minimum allowed!")
            await interaction.response.send_message("Your must set your reminder to at least 10 seconds.", ephemeral=True)

            return

        if message and len(message) > Config.REMINDERS_MAX_MESSAGE_LEN:
            self.l.warning("Reminder message exceeded maximum length!")
            await interaction.response.send_message("Your message must not exceed 100 characters.", ephemeral=True)
            return

        if not interaction.channel or not SimpleUtils.is_guild_channel(interaction.channel):
            self.l.error("Interaction channel was not found or is not a guild channel.")
            return

        trigger = int(time.time() + seconds)

        reminder_id = str(uuid.uuid4())[:8]
        reminder = {
            "id": reminder_id,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,
            "guild_id": interaction.guild_id,
            "message": message,
            "trigger": trigger,
        }

        self.reminders[reminder_id] = reminder
        self.l.info(f"Created reminder {reminder_id} for user {interaction.user.id}.")

        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.l.info(f"Reminders data saved after creating reminder {reminder_id}.")

        await self._schedule_reminder(reminder)

        await interaction.response.send_message(content=f"I will remind you **<t:{trigger}:R>**.", ephemeral=True)

    @group.command(name="list", description="Show a list of your currently scheduled reminders.")
    async def reminder_list(self, interaction: Interaction) -> None:
        self.l.info(f"Received reminder list command from user {interaction.user.id}.")

        user_reminders = [r for r in self.reminders.values() if r["user_id"] == interaction.user.id]

        if not user_reminders:
            self.l.info(f"User {interaction.user.id} has no reminders.")
            await interaction.response.send_message(content="You have no reminders.", ephemeral=True)
            return

        lines = [f'- **{r["id"]}** <t:{int(r["trigger"])}:R> "{r["message"]}"' for r in user_reminders]

        self.l.debug(f"Listing {len(user_reminders)} reminders for user {interaction.user.id}.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="cancel", description="Cancel an existing reminder.")
    @app_commands.describe(reminder_id="The ID of the reminder you want to cancel.")
    async def reminder_cancel(self: t.Self, interaction: Interaction, reminder_id: int) -> None:
        self.l.info(f"Received reminder cancel command for reminder {reminder_id} from user {interaction.user.id}.")

        reminder = self.reminders[reminder_id]

        if not reminder or reminder["user_id"] != interaction.user.id:
            self.l.warning(f"Reminder {reminder_id} not found or not owned by user {interaction.user.id}!")
            await interaction.response.send_message("Reminder not found.", ephemeral=True)
            return

        task = self.tasks.pop(reminder_id, None)

        if task:
            self.l.debug(f"Cancelling task for reminder {reminder_id}.")
            task.cancel()

        self.reminders.pop(reminder_id, None)
        self.l.info(f"Reminder {reminder_id} cancelled and removed.")

        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.l.info(f"Reminders data saved after cancelling reminder {reminder_id}.")

        await interaction.response.send_message("Reminder cancelled.", ephemeral=True)

    @group.command(name="edit", description="Edit the message of an existing reminder.")
    @app_commands.describe(reminder_id="The ID of the reminder you want to edit.", new_message="The new reminder message (max 100 characters).")
    async def reminder_edit(self: t.Self, interaction: Interaction, reminder_id: int, new_message: str) -> None:
        self.l.info(f"Received reminder edit command for reminder {reminder_id} from user {interaction.user.id}.")

        reminder = self.reminders[reminder_id]

        if not reminder or reminder["user_id"] != interaction.user.id:
            self.l.warning(f"Reminder {reminder_id} not found or not owned by user {interaction.user.id}!")
            await interaction.response.send_message("Reminder not found.", ephemeral=True)
            return

        reminder["message"] = new_message
        self.l.info(f"Reminder {reminder_id} message updated by user {interaction.user.id}.")

        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.l.info(f"Reminders data saved after editing reminder {reminder_id}.")

        await interaction.response.send_message("Reminder updated.", ephemeral=True)

    async def _initialize_scheduler(self) -> None:
        self.l.info("Initializing reminder scheduler...")

        await self.bot.wait_until_ready()

        self._load_data()

        for reminder in self.reminders.values():
            self.l.debug(f"Scheduling reminder {reminder['id']} from disk...")
            await self._schedule_reminder(reminder)

        self.l.info("Reminder scheduler ready.")

    async def _schedule_reminder(self, reminder: T_DATA) -> None:
        reminder_id = reminder["id"]

        if reminder_id in self.tasks:
            self.l.debug(f"Cancelling existing task for reminder {reminder_id}...")
            self.tasks[reminder_id].cancel()

        self.l.info(f"Scheduling reminder task for reminder {reminder_id}...")
        task = asyncio.create_task(self._worker_task(reminder_id))
        self.tasks[reminder_id] = task

    async def _worker_task(self: t.Self, reminder_id: T_NUMERIC) -> None:
        self.l.info(f"Reminder worker started for reminder {reminder_id}.")

        reminder = self.reminders[reminder_id]

        if not reminder:
            self.l.warning(f"Reminder {reminder_id} not found! Stopping worker...")
            return

        delay = int(reminder["trigger"] - time.time())

        if delay > 0:
            self.l.debug(f"Sleeping for {delay} seconds before triggering reminder {reminder_id}...")
            await asyncio.sleep(delay)

        channel = self.bot.get_channel(reminder["channel_id"])

        if not channel:
            self.l.debug(f"Channel {reminder['channel_id']} was not found in cache. Attempting to fetch...")

            try:
                channel = await self.bot.fetch_channel(reminder["channel_id"])
            except Exception:
                self.l.exception("Failed to fetch channel!")
                return

        try:
            message = f'<@{reminder["user_id"]}>\n-# Message: "**{reminder["message"] or "None"}**"\n-# Reminder ID: **{reminder["id"]}**'

            if not SimpleUtils.is_messageable(channel):
                self.l.warning(f"Channel {channel.id} is not messageable!")
                return

            await channel.send(message)
            self.l.info(f"Reminder {reminder_id} sent.")

        except Exception:
            self.l.exception(f"Failed to send reminder {reminder_id}.")

        self.reminders.pop(reminder_id, None)
        self.tasks.pop(reminder_id, None)

        self.l.debug(f"Reminder {reminder_id} removed from memory and tasks.")
        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.l.info(f"Reminders data saved after sending reminder {reminder_id}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReminderCog(bot))

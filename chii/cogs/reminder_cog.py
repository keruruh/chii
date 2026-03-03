import asyncio
import json
import pathlib
import time
import typing as t
import uuid

from discord import Interaction, app_commands
from discord.ext import commands

from chii.config import Config
from chii.utils import T_DATA, T_NUMERIC, LogSubclass, SimpleUtils


class ReminderCog(LogSubclass, commands.Cog):
    group = app_commands.Group(name="reminder", description="Manage personal reminders and scheduled notifications.")

    def __init__(self: t.Self, bot: commands.Bot) -> None:
        self.bot = bot
        self.reminders = {}
        self.tasks = {}

        self.log.info("Starting ReminderCog scheduler initialization...")
        self.bot.loop.create_task(self._initialize_scheduler())
        self.log.info("ReminderCog initialized.")

    def _load_data(self) -> None:
        if not Config.REMINDERS_DATA_PATH.exists():
            self.log.info(f'Reminders data file not found at "{Config.REMINDERS_DATA_PATH}". Creating new data file...')
            SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, [])
            return

        self.log.debug(f'Loading reminders from "{Config.REMINDERS_DATA_PATH}"...')

        try:
            with pathlib.Path(Config.REMINDERS_DATA_PATH).open(encoding="utf-8") as f:
                data = json.load(f)

            for r in data:
                self.reminders[r["uuid"]] = r

            self.log.info(f"Loaded {len(self.reminders)} reminders from disk.")

        except Exception:
            self.log.exception("Failed loading reminders!")

    async def cog_unload(self) -> None:
        self.log.info("Unloading ReminderCog and cancelling all reminder tasks...")

        for task in self.tasks.values():
            task.cancel()

        self.tasks.clear()
        self.log.info("All reminder tasks have been cancelled.")

    @group.command(name="set", description="Create a new reminder that will notify you after a specified time.")
    @app_commands.describe(time_input="The time after which the reminder should trigger (e.g.: 10s, 5m, 1h, 3d).", message="Custom message. (Optional)")
    async def reminder_set(self: t.Self, interaction: Interaction, time_input: str, message: str | None) -> None:
        self.log.info(f"Received reminder set command from user {interaction.user.id}.")

        user_reminders = [r for r in self.reminders.values() if r["user_id"] == interaction.user.id]

        if len(user_reminders) >= Config.REMINDERS_MAX_COUNT:
            await interaction.response.send_message(
                f"You have reached the maximum reminder limit ({Config.REMINDERS_MAX_COUNT}). Please delete one before creating a new reminder.",
                ephemeral=True,
            )

            return

        try:
            seconds = SimpleUtils.parse_time(time_input)
        except Exception:
            self.log.exception("Invalid time format provided for reminder.")
            await interaction.response.send_message("Invalid time format.", ephemeral=True)
            return

        if seconds < Config.REMINDERS_MIN_TIME_SEC:
            self.log.warning("Reminder time below minimum allowed!")
            await interaction.response.send_message("Your must set your reminder to at least 10 seconds.", ephemeral=True)

            return

        if message and len(message) > Config.REMINDERS_MAX_MESSAGE_LEN:
            self.log.warning("Reminder message exceeded maximum length!")
            await interaction.response.send_message("Your message must not exceed 100 characters.", ephemeral=True)
            return

        if not interaction.channel or not SimpleUtils.is_guild_channel(interaction.channel):
            self.log.error("Interaction channel was not found or is not a guild channel.")
            return

        trigger = int(time.time() + seconds)
        reminder_uuid = str(uuid.uuid4())[:8]
        reminder = {
            "uuid": reminder_uuid,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,
            "guild_id": interaction.guild_id,
            "message": message,
            "trigger": trigger,
        }

        self.reminders[reminder_uuid] = reminder
        self.log.info(f"Created reminder {reminder_uuid} (ID) for user {interaction.user.id} (ID).")

        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.log.info(f"Reminders data saved after creating reminder {reminder_uuid} (ID).")

        await self._schedule_reminder(reminder)

        await interaction.response.send_message(content=f"I will remind you **<t:{trigger}:R>**.", ephemeral=True)

    @group.command(name="list", description="Show a list of your currently scheduled reminders.")
    async def reminder_list(self: t.Self, interaction: Interaction) -> None:
        self.log.info(f"Received reminder list command from user {interaction.user.id}.")

        user_reminders = [r for r in self.reminders.values() if r["user_id"] == interaction.user.id]

        if not user_reminders:
            self.log.info(f"User {interaction.user.id} has no reminders.")
            await interaction.response.send_message(content="You have no reminders.", ephemeral=True)
            return

        lines = [f'- **{r["uuid"]}** <t:{int(r["trigger"])}:R> "{r["message"]}"' for r in user_reminders]
        footer = f"\n\n-# **Total**: {len(user_reminders)}/{Config.REMINDERS_MAX_COUNT}"

        self.log.debug(f"Listing {len(user_reminders)} reminders for user {interaction.user.id}.")
        await interaction.response.send_message("\n".join(lines) + footer, ephemeral=True)

    @group.command(name="cancel", description="Cancel an existing reminder.")
    @app_commands.describe(reminder_uuid="The UUID of the reminder you want to cancel.")
    async def reminder_cancel(self: t.Self, interaction: Interaction, reminder_uuid: str) -> None:
        self.log.info(f"Received reminder cancel command for reminder {reminder_uuid} (ID) from user {interaction.user.id} (ID).")

        reminder = self.reminders.get(reminder_uuid)

        if not reminder or reminder["user_id"] != interaction.user.id:
            self.log.warning(f"Reminder {reminder_uuid} (ID) not found or not owned by user {interaction.user.id} (ID)!")
            await interaction.response.send_message("Reminder not found.", ephemeral=True)
            return

        task = self.tasks.pop(reminder_uuid, None)

        if task:
            self.log.debug(f"Cancelling task for reminder {reminder_uuid} (ID).")
            task.cancel()

        self.reminders.pop(reminder_uuid, None)
        self.log.info(f"Reminder {reminder_uuid} (ID) cancelled and removed.")

        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.log.info(f"Reminders data saved after cancelling reminder {reminder_uuid} (ID).")

        await interaction.response.send_message("Reminder cancelled.", ephemeral=True)

    @group.command(name="edit", description="Edit the message of an existing reminder.")
    @app_commands.describe(reminder_uuid="The UUID of the reminder you want to edit.", new_message="The new reminder message (max 100 characters).")
    async def reminder_edit(self: t.Self, interaction: Interaction, reminder_uuid: str, new_message: str) -> None:
        self.log.info(f"Received reminder edit command for reminder {reminder_uuid} (ID) from user {interaction.user.id} (ID).")

        reminder = self.reminders[reminder_uuid]

        if not reminder or reminder["user_id"] != interaction.user.id:
            self.log.warning(f"Reminder {reminder_uuid} (ID) not found or not owned by user {interaction.user.id} (ID)!")
            await interaction.response.send_message("Reminder not found.", ephemeral=True)
            return

        if new_message and len(new_message) > Config.REMINDERS_MAX_MESSAGE_LEN:
            self.log.warning("Reminder message exceeded maximum length!")
            await interaction.response.send_message("Your message must not exceed 100 characters.", ephemeral=True)
            return

        reminder["message"] = new_message
        self.log.info(f"Reminder {reminder_uuid} (ID) message updated by user {interaction.user.id} (ID).")

        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.log.info(f"Reminders data saved after editing reminder {reminder_uuid} (ID).")

        await interaction.response.send_message("Reminder updated.", ephemeral=True)

    async def _initialize_scheduler(self) -> None:
        self.log.info("Initializing reminder scheduler...")

        await self.bot.wait_until_ready()

        self._load_data()

        for reminder in self.reminders.values():
            self.log.debug(f"Scheduling reminder {reminder['uuid']} from disk...")
            await self._schedule_reminder(reminder)

        self.log.info("Reminder scheduler ready.")

    async def _schedule_reminder(self: t.Self, reminder: T_DATA) -> None:
        reminder_uuid = reminder["uuid"]

        if reminder_uuid in self.tasks:
            self.log.debug(f"Cancelling existing task for reminder {reminder_uuid} (ID)...")
            self.tasks[reminder_uuid].cancel()

        self.log.info(f"Scheduling reminder task for reminder {reminder_uuid} (ID)...")
        task = asyncio.create_task(self._worker_task(reminder_uuid))
        self.tasks[reminder_uuid] = task

    async def _worker_task(self: t.Self, reminder_uuid: str) -> None:
        self.log.info(f"Reminder worker started for reminder {reminder_uuid} (ID).")

        reminder = self.reminders[reminder_uuid]

        if not reminder:
            self.log.warning(f"Reminder {reminder_uuid} (ID) not found! Stopping worker...")
            return

        delay = int(reminder["trigger"] - time.time())

        if delay > 0:
            self.log.debug(f"Sleeping for {delay} seconds before triggering reminder {reminder_uuid} (ID)...")
            await asyncio.sleep(delay)

        channel = self.bot.get_channel(reminder["channel_id"])

        if not channel:
            self.log.debug(f"Channel {reminder['channel_id']} was not found in cache. Attempting to fetch...")

            try:
                channel = await self.bot.fetch_channel(reminder["channel_id"])
            except Exception:
                self.log.exception("Failed to fetch channel!")
                return

        try:
            message = f"<@{reminder['user_id']}>\n-# Message: **{reminder['message'] or 'None'}**\n-# Reminder UUID: **{reminder['uuid']}**"

            if not SimpleUtils.is_messageable(channel):
                self.log.warning(f"Channel {channel.id} is not messageable!")
                return

            await channel.send(message)
            self.log.info(f"Reminder {reminder_uuid} (ID) sent.")

        except Exception:
            self.log.exception(f"Failed to send reminder {reminder_uuid} (ID).")

        self.reminders.pop(reminder_uuid, None)
        self.tasks.pop(reminder_uuid, None)

        self.log.debug(f"Reminder {reminder_uuid} (ID) removed from memory and tasks.")
        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self.log.info(f"Reminders data saved after sending reminder {reminder_uuid} (ID).")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReminderCog(bot))

import asyncio
import json
import logging
import time
import uuid

import discord
import discord.ext.commands

from chii.config import Config
from chii.utils import SimpleUtils

class ReminderCog(discord.ext.commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = discord.app_commands.Group(name="reminder", description="Reminder commands.")

    def __init__(self, bot: discord.ext.commands.Bot) -> None:
        self.bot = bot
        self.reminders = {}
        self.tasks = {}

        self.bot.loop.create_task(self._initialize_scheduler())

        self.l.info("ReminderCog initialized.")

    async def cog_unload(self) -> None:
        for task in self.tasks.values():
            task.cancel()

        self.tasks.clear()
        self.l.info("All reminder tasks have been cancelled.")

    def _load_data(self) -> None:
        if not Config.REMINDERS_DATA_PATH.exists():
            return
        try:
            with open(Config.REMINDERS_DATA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            for r in data:
                self.reminders[r["id"]] = r

            self.l.info(f"Loaded {len(self.reminders)} reminders from disk.")

        except Exception:
            self.l.exception("Failed loading reminders.")

    def _schedule_reminder(self, reminder: SimpleUtils.JSON) -> None:
        reminder_id = reminder["id"]

        if reminder_id in self.tasks:
            self.tasks[reminder_id].cancel()

        task = asyncio.create_task(self.reminder_worker(reminder_id))
        self.tasks[reminder_id] = task

    async def _initialize_scheduler(self) -> None:
        await self.bot.wait_until_ready()

        self._load_data()

        for reminder in self.reminders.values():
            self._schedule_reminder(reminder)

        self.l.info("Reminder scheduler ready.")

    @staticmethod
    def parse_time(time_string: str) -> float:
        unit = time_string[-1].lower()
        value = float(time_string[:-1])

        match unit:
            case "s":
                return value
            case "m":
                return value * 60
            case "h":
                return value * 3600
            case "d":
                return value * 86400
            case _:
                raise ValueError("Invalid time format")

    async def reminder_worker(self, reminder_id: str) -> None:
        reminder = self.reminders.get(reminder_id)

        if not reminder:
            return

        delay = int(reminder["trigger"] - time.time())

        if delay > 0:
            await asyncio.sleep(delay)

        channel = self.bot.get_channel(reminder["channel_id"])

        if channel is None:
            try:
                channel = await self.bot.fetch_channel(reminder["channel_id"])
            except Exception as e:
                self.l.error(f"Failed to fetch channel: {e}")
                return

        try:
            message = (
                f"<@{reminder["user_id"]}>\n"
                f"-# Message: \"**{reminder["message"] if reminder["message"] else "None"}**\"\n"
                f"-# Reminder ID: **{reminder["id"]}**"
            )

            if not SimpleUtils.is_messageable(channel):
                self.l.warning(f"Channel {channel.id} is not messageable.")
                return
            else:
                await channel.send(message) # type: ignore
                self.l.info(f"Reminder {reminder_id} sent.")

        except Exception:
            self.l.exception(f"Failed to send reminder {reminder_id}.")

        self.reminders.pop(reminder_id, None)
        self.tasks.pop(reminder_id, None)
        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))

    @group.command(name="set", description="Create a reminder.")
    @discord.app_commands.describe(time_input="The time of the reminder. (10m, 60s, 1h, 3d)", message="Custom message. (Optional)")
    async def reminder_set(self, interaction: discord.Interaction, time_input: str, message: str | None) -> None:
        try:
            seconds = self.parse_time(time_input)
        except Exception:
            await interaction.response.send_message("Invalid time format.", ephemeral=True)
            return

        if seconds < 10:
            await interaction.response.send_message("Your must set your reminder to at least 10 seconds.", ephemeral=True)
            return

        if message and len(message) > 100:
            await interaction.response.send_message("Your message must not exceed 100 characters.", ephemeral=True)
            return

        if not interaction.channel:
            self.l.error("Something went wrong. Interaction channel was not found.")
            return

        trigger = int(time.time() + seconds)

        reminder_id = str(uuid.uuid4())[:8]
        reminder = {
            "id": reminder_id,
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id,
            "guild_id": interaction.guild_id,
            "message": message,
            "trigger": int(time.time() + seconds),
        }

        self.reminders[reminder_id] = reminder
        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))
        self._schedule_reminder(reminder)

        await interaction.response.send_message(f"I will remind you **<t:{trigger}:R>**.")

    @group.command(name="list", description="List your current reminders.")
    async def reminder_list(self, interaction: discord.Interaction) -> None:
        user_reminders = [r for r in self.reminders.values() if r["user_id"] == interaction.user.id]

        if not user_reminders:
            await interaction.response.send_message("You have no reminders.", ephemeral=True)
            return

        lines = []

        for r in user_reminders:
            lines.append(f"- **{r["id"]}** <t:{int(r["trigger"])}:R> \"{r["message"]}\"")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="cancel", description="Cancel a reminder.")
    @discord.app_commands.describe(reminder_id="The ID of the reminder you want to canel.")
    async def reminder_cancel(self, interaction: discord.Interaction, reminder_id: str) -> None:
        reminder = self.reminders.get(reminder_id)

        if not reminder or reminder["user_id"] != interaction.user.id:
            await interaction.response.send_message("Reminder not found.", ephemeral=True)
            return

        task = self.tasks.pop(reminder_id, None)

        if task:
            task.cancel()

        self.reminders.pop(reminder_id, None)
        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))

        await interaction.response.send_message("Reminder cancelled.", ephemeral=True)

    @group.command(name="edit", description="Edit a reminder's message.")
    @discord.app_commands.describe(reminder_id="The ID of the reminder you want to edit.", new_message="The new message.")
    async def reminder_edit(self, interaction: discord.Interaction, reminder_id: str, new_message: str) -> None:
        reminder = self.reminders.get(reminder_id)

        if not reminder or reminder["user_id"] != interaction.user.id:
            await interaction.response.send_message("Reminder not found.", ephemeral=True)
            return

        reminder["message"] = new_message
        SimpleUtils.save_data(Config.REMINDERS_DATA_PATH, list(self.reminders.values()))

        await interaction.response.send_message("Reminder updated.", ephemeral=True)

async def setup(bot: discord.ext.commands.Bot) -> None:
    await bot.add_cog(ReminderCog(bot))

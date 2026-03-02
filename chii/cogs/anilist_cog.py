import contextlib
import enum
import json
import logging
import pathlib

import aiohttp
from discord import Color, Embed, Interaction, Member, TextChannel, Thread, app_commands
from discord.ext import commands, tasks

from chii.config import Config
from chii.utils import T_CHANNEL, T_DATA, T_NUMERIC, SimpleUtils


class _Status(enum.Enum):
    COMPLETED = "Completed"
    PAUSED = "Paused"
    DROPPED = "Dropped"
    WATCHED = "Watched"
    REWATCHED = "Rewatched"
    READ = "Read"
    REREAD = "Reread"


class AniListCog(commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = app_commands.Group(name="anilist", description="Manage and track AniList activity for Discord users.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session = aiohttp.ClientSession()

        self.l.info("Starting AniListCog background update task...")
        self.normal_updates.start()
        self.l.info("AniListCog initialized.")

    def _load_data(self) -> T_DATA:
        default_data = {
            "channel_id": None,
            "users": {},
        }

        if not Config.ANILIST_DATA_PATH.exists():
            self.l.info(f'AniList data file not found at "{Config.ANILIST_DATA_PATH}".')
            self.l.info("Creating new data file...")

            SimpleUtils.save_data(Config.ANILIST_DATA_PATH, default_data)

            return default_data.copy()

        self.l.debug(f'Loading AniList data from "{Config.ANILIST_DATA_PATH}"...')

        with pathlib.Path(Config.ANILIST_DATA_PATH).open(encoding="utf-8") as f:
            return json.load(f)

    async def cog_unload(self) -> None:
        self.l.info("Unloading AniListCog and stopping background tasks...")

        self.normal_updates.cancel()

        if self.debug_updates.is_running():
            self.l.info("Stopping debug update task...")
            self.debug_updates.cancel()

        await self.session.close()
        self.l.info("Closed AIOHTTP session.")

    @tasks.loop(seconds=Config.ANILIST_NORMAL_UPDATES_TIME_SEC)
    async def normal_updates(self) -> None:
        self.l.debug("Normal update loop triggered.")

        await self.bot.wait_until_ready()
        await self.run_update_cycle()

    @tasks.loop(seconds=Config.ANILIST_DEBUG_UPDATES_TIME_SEC)
    async def debug_updates(self) -> None:
        self.l.debug("Debug update loop triggered.")

        await self.bot.wait_until_ready()
        await self.run_update_cycle()

    @group.command(name="force", description="Manually force an AniList update check for all linked users.")
    @commands.is_owner()
    async def anilist_force(self, interaction: Interaction) -> None:
        self.l.info("Manual AniList update forced by owner.")

        await interaction.response.defer(ephemeral=True)
        await self.run_update_cycle()
        await interaction.followup.send("Manual update executed.", ephemeral=True)

    @group.command(name="channel", description="Set the channel where AniList activity updates will be posted.")
    @app_commands.describe(channel="The text channel that will receive AniList update notifications.")
    @commands.is_owner()
    async def anilist_channel(self, interaction: Interaction, channel: TextChannel) -> None:
        self.l.info(f"Setting AniList notification channel to {channel.id}...")

        data = self._load_data()
        data["channel_id"] = channel.id

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)
        self.l.info(f"AniList notification channel set to {channel.id} and saved.")

        await interaction.response.send_message(f"Set {channel.mention} as AniList notification channel.", ephemeral=True)

    @group.command(name="link", description="Link a Discord user to their AniList account for activity tracking.")
    @app_commands.describe(member="The Discord member whose AniList account will be linked.", username="The AniList username to track.")
    @commands.is_owner()
    async def anilist_link(self, interaction: Interaction, member: Member, username: str) -> None:
        self.l.info(f'Linking Discord user {member.id} to AniList username "{username}".')

        await interaction.response.defer(ephemeral=True)

        data = self._load_data()
        data["users"][str(member.id)] = {
            "anilist": username,
            "last_activity_at": None,
            "last_activity_id": None,
            "last_message_id": None,
            "progress_cache": {},
            "streak": 0,
            "synced": False,
        }

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)
        self.l.info(f'Linked Discord user {member.id} to AniList username "{username}" and saved.')

        await interaction.followup.send(f"Linked {member.mention} to [{username}](<https://anilist.co/user/{username}>).", ephemeral=True)

    async def run_update_cycle(self) -> None:
        self.l.info("Starting AniList update cycle...")

        data = self._load_data()
        users = data.get("users", {})

        if not users:
            self.l.info("No users linked for AniList tracking.")
            return

        channel = self.get_notification_channel(data["channel_id"])

        if not channel:
            return

        batch, alias_map = await self.fetch_activity_batch(users)

        if not batch:
            self.l.warning("No activity data returned from AniList API!")
            return

        if not alias_map:
            self.l.warning("No alias map returned from AniList API!")
            return

        for alias, activity in batch.items():
            if not activity:
                continue

            discord_id = alias_map[alias]
            user_data = users[discord_id]

            if not await self.process_activity(channel, discord_id, user_data, activity):
                continue

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)
        self.l.info("AniList update cycle completed.")

    def get_notification_channel(self, channel_id: int) -> T_CHANNEL:
        if not channel_id:
            self.l.warning("AniList notification channel is not set!")
            return None

        channel = self.bot.get_channel(channel_id)

        if not channel or not SimpleUtils.is_messageable(channel):
            self.l.warning("Notification channel is not messageable!")
            return None

        return channel

    async def fetch_activity_batch(self, users: T_DATA) -> tuple[T_DATA | None, T_DATA | None]:
        active_users = {}
        alias_map = {}

        for i, (discord_id, u) in enumerate(users.items()):
            alias = f"user_{i}"
            active_users[alias] = u["anilist"]
            alias_map[alias] = discord_id

        self.l.debug(f'Fetching batch activity for users: "{active_users}"...')

        user_parts = []

        for alias, name in active_users.items():
            user_parts.append(f"""
                {alias}: User(name: "{name}") {{
                    id
                    name
                }}
            """)

        query = f"query {{ {' '.join(user_parts)} }}"
        users_data = await self.query_graphql(query)

        if not users_data:
            self.l.warning("No user data returned from AniList API for batch activity!")
            return None, None

        id_map = {alias: payload["id"] for alias, payload in users_data.items() if payload}

        if not id_map:
            self.l.warning("No valid user IDs found in AniList API response!")
            return None, None

        activity_parts = []

        for alias, user_id in id_map.items():
            activity_parts.append(f"""
                {alias}: Activity(userId: {user_id}, sort: ID_DESC) {{
                    ... on ListActivity {{
                        id
                        createdAt
                        progress
                        status

                        media {{
                            id
                            idMal
                            title {{ romaji }}
                        }}

                        user {{
                            id
                            name
                            avatar {{ medium }}
                        }}
                    }}
                }}
            """)

        query = f"query {{ {' '.join(activity_parts)} }}"

        self.l.debug("Querying AniList API for user activities...")
        batch = await self.query_graphql(query)

        return batch, alias_map

    async def query_graphql(self, query: str, variables: T_DATA | None = None) -> T_DATA | None:
        payload = {
            "url": "https://graphql.anilist.co",
            "json": {
                "query": query,
                "variables": variables or {},
            },
        }

        if not variables:
            self.l.debug("Sending GraphQL query to AniList API with no variables.")
        else:
            self.l.debug(f'Sending GraphQL query to AniList API with variables: "{variables}"...')

        try:
            async with self.session.post(**payload) as response:
                ok = 200

                if response.status != ok:
                    text = await response.text()
                    self.l.error(f"AniList API Error {response.status}: {text}")
                    return None

                data = await response.json()

                if "errors" in data:
                    self.l.error(f'AniList GraphQL Error: {data["errors"]}')
                    return None

                self.l.info("Retrieved data from AniList.")
                return data["data"]

        except Exception:
            self.l.exception("AniList API Exception!")
            return None

    async def process_activity(self, channel: T_CHANNEL, discord_id: T_NUMERIC, user_data: T_DATA, activity: T_DATA) -> bool:
        activity_id = activity["id"]
        last_seen = user_data["last_activity_id"]

        if not user_data["synced"]:
            self.l.info(f"Syncing user data for member {discord_id}.")
            user_data.update({"last_activity_id": activity_id, "synced": True})

            return False

        if last_seen and activity_id <= last_seen:
            self.l.debug(f"No new activity for member {discord_id}.")
            return False

        user_data["last_activity_id"] = activity_id

        if not self.is_real_progress(user_data, activity):
            self.l.debug(f"Activity for {discord_id} is not real progress.")
            return False

        self.update_streak(user_data, activity["createdAt"])

        embed = await self.build_embed(discord_id, user_data, activity)
        await self.send_update(channel, user_data, embed)

        return True

    def is_real_progress(self, user_data: T_DATA, activity: T_DATA) -> bool:
        if not self.is_consumption_activity(activity):
            self.l.debug("Activity is not a consumption activity. Skipping progress check...")
            return False

        media_id = str(activity["media"]["id"])
        new_progress = self.extract_progress(activity)

        if not new_progress:
            status = self.extract_status(activity)

            if status and status in {_Status.COMPLETED, _Status.DROPPED, _Status.PAUSED}:
                self.l.info("Activity has no numeric progress but it is supported.")
                return True

            self.l.info("Activity has no numeric progress.")
            return False

        cache = user_data.setdefault("progress_cache", {})
        old_progress = cache.get(media_id)
        cache[media_id] = new_progress

        if not old_progress:
            self.l.info(f"Initial cache set for media {media_id}.")
            return True

        if new_progress > old_progress:
            self.l.info(f"Progress of media {media_id} increased from {old_progress} to {new_progress}.")
            return True

        self.l.debug(f"No progress increase for media {media_id}. Current: {new_progress}, Previous: {old_progress}.")
        return False

    def update_streak(self, user_data: T_DATA, timestamp: int) -> None:
        last = user_data["last_activity_at"]

        if not last:
            user_data["streak"] = 1
            self.l.info("New streak started for user.")

        else:
            last_day = last // 86400
            new_day = timestamp // 86400

            if new_day == last_day:
                self.l.debug("Activity occurred on the same day. Streak is not changed.")
                return

            if new_day - last_day == 1:
                user_data["streak"] += 1
                self.l.info(f'Streak incremented to {user_data["streak"]}.')

            else:
                user_data["streak"] = 1
                self.l.info("Streak reset to 1.")

        user_data["last_activity_at"] = timestamp
        self.l.debug(f'Updated "last_activity_at" to {timestamp}.')

    async def build_embed(self, discord_id: T_NUMERIC, user_data: T_DATA, activity: T_DATA) -> Embed:
        title = activity["media"]["title"]["romaji"]
        status = self.extract_status(activity)
        progress = None

        status_color_map = {
            _Status.COMPLETED: Color.green(),
            _Status.DROPPED: Color.orange(),
            _Status.PAUSED: Color.red(),
        }

        if status in status_color_map:
            title = f"{status.value} {title}"
            color = status_color_map[status]
        else:
            progress = self.extract_progress(activity)
            color = Color.ash_theme()

        parts = [
            f'{(_Status.value if status else "Unknown")}: **{progress}**\n' if progress else None,
            f'Current Streak: **{user_data["streak"]}** {"day" if user_data["streak"] == 1 else 'days'}\n\n',
            f'[**AniList**](https://anilist.co/anime/{activity["media"]["id"]}) | ',
            f'[**MyAnimeList**](https://myanimelist.net/anime/{activity["media"]["idMal"]})\n\n',
            f'<t:{activity["createdAt"]}:R>',
        ]

        embed = Embed(color=color, title=title, description="".join(p for p in parts if p))

        user = await self.bot.fetch_user(int(discord_id))

        author_name = f'{activity["user"]["name"]} (@{user.name})' if user else activity["user"]["name"]
        author_url = f'https://anilist.co/user/{activity["user"]["id"]}'
        author_icon = activity["user"]["avatar"]["medium"]

        embed.set_author(name=author_name, url=author_url, icon_url=author_icon)

        return embed

    async def send_update(self, channel: T_CHANNEL, user_data: T_DATA, embed: Embed) -> None:
        old_message_id = user_data["last_message_id"]

        if not channel or not SimpleUtils.is_messageable(channel):
            self.l.warning("An invalid channel was supplied!")
            return

        if old_message_id:
            with contextlib.suppress(Exception):
                if isinstance(channel, (TextChannel, Thread)):
                    await channel.get_partial_message(old_message_id).delete()

        message = await channel.send(embed=embed)
        user_data["last_message_id"] = message.id

    def is_consumption_activity(self, activity: T_DATA) -> bool:
        status = self.extract_status(activity)

        if not status:
            self.l.info(f'Ignoring non-consumption activity: "{status}".')
            return False

        self.l.debug(f'Activity "{status}" is a valid consumption activity.')
        return True

    def extract_status(self, activity: T_DATA) -> _Status | None:
        status = activity.get("status", "").capitalize()

        try:
            return _Status(status)
        except ValueError:
            self.l.warning(f'Unsupported status "{status}" found!')
            return None

    def extract_progress(self, activity: T_DATA) -> int | None:
        raw = activity["progress"]

        if not raw:
            self.l.debug("No progress field found in activity.")
            return None

        try:
            text = str(raw).strip()

            if "-" in text:
                text = text.split("-")[-1].strip()

            progress = int(text)
            self.l.debug(f"Extracted progress value of {progress}.")

        except (ValueError, TypeError):
            self.l.warning(f'Failed to extract numeric progress from raw value "{raw}"!')
            return None

        else:
            return progress


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AniListCog(bot))

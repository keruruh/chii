import json
import logging
import pathlib

import aiohttp
from discord import Color, Embed, Interaction, Member, TextChannel, app_commands
from discord.ext import commands, tasks

from chii.config import Config
from chii.utils import JSON, SimpleUtils


class AniListCog(commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = app_commands.Group(name="anilist", description="Manage and track AniList activity for Discord users.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.session = aiohttp.ClientSession()

        self.l.info("Starting AniListCog background update task...")
        self._check_updates.start()
        self.l.info("AniListCog initialized.")

    async def cog_unload(self) -> None:
        self.l.info("Unloading AniListCog and stopping background tasks...")

        self._check_updates.cancel()

        if self._debug_updates.is_running():
            self.l.info("Stopping debug update task...")
            self._debug_updates.cancel()

        await self.session.close()
        self.l.info("Closed aiohttp session.")

    def _load_data(self) -> JSON:
        default_data = {
            "channel_id": None,
            "users": {},
        }

        if not Config.ANILIST_DATA_PATH.exists():
            self.l.info(f"AniList data file not found at {Config.ANILIST_DATA_PATH}.")
            self.l.info("Creating new data file...")

            SimpleUtils.save_data(Config.ANILIST_DATA_PATH, default_data)

            return default_data.copy()

        self.l.debug(f"Loading AniList data from {Config.ANILIST_DATA_PATH}...")

        with pathlib.Path(Config.ANILIST_DATA_PATH).open(encoding="utf-8") as f:
            return json.load(f)

    async def _query_graphql(self, query: str, variables: JSON | None = None) -> JSON | None:
        api_url = "https://graphql.anilist.co"
        payload = {
            "url": api_url,
            "json": {
                "query": query,
                "variables": variables or {},
            },
        }

        self.l.debug(f"Sending GraphQL query to AniList API with variables: {variables}...")

        try:
            async with self.session.post(**payload) as response:
                ok = 200

                if response.status != ok:
                    text = await response.text()
                    self.l.error(f"AniList API Error {response.status}: {text}.")
                    return None

                data = await response.json()

                if "errors" in data:
                    self.l.error(f"AniList GraphQL Error: {data['errors']}.")
                    return None

                self.l.info("Retrieved data from AniList.")
                return data.get("data")

        except Exception as e:
            self.l.warning(f"AniList API Exception: {e}.")
            return None

    def _is_consumption_activity(self, activity: JSON) -> bool:
        status = (activity.get("status") or "").lower()
        valid_prefixes = (
            "watched",
            "rewatched",
            "read",
            "reread",
        )

        if not status.startswith(valid_prefixes):
            self.l.info(f'Ignoring non-consumption activity "{status}".')
            return False

        self.l.debug(f'Activity "{status}" is a valid consumption activity.')
        return True

    def _extract_progress(self, activity: JSON) -> int | None:
        raw = activity.get("progress")

        if raw is None:
            self.l.debug("No progress field found in activity.")
            return None

        try:
            text = str(raw).strip()

            if "-" in text:
                text = text.split("-")[-1].strip()

            progress = int(text)
            self.l.debug(f"Extracted progress value of {progress}.")

        except (ValueError, TypeError):
            self.l.warning(f'Failed to extract numeric progress from raw value "{raw}".')
            return None

        else:
            return progress

    def _is_real_progress(self, user_data: JSON, activity: JSON) -> bool:
        if not self._is_consumption_activity(activity):
            self.l.debug("Activity is not a consumption activity. Skipping progress check...")
            return False

        media_id = str(activity["media"]["id"])
        new_progress = self._extract_progress(activity)

        if new_progress is None:
            self.l.info("Activity has no numeric progress.")
            return False

        cache = user_data.setdefault("progress_cache", {})
        old_progress = cache.get(media_id)
        cache[media_id] = new_progress

        if old_progress is None:
            self.l.info(f"Initial cache set for media {media_id}.")
            return True

        if new_progress > old_progress:
            self.l.info(f"Progress of media {media_id} increased from {old_progress} to {new_progress}.")
            return True

        self.l.debug(f"No progress increase for media {media_id}. Current: {new_progress}, Previous: {old_progress}.")
        return False

    def _update_streak(self, user_data: JSON, timestamp: int) -> None:
        last = user_data.get("last_activity_at", None)

        if not last:
            user_data["streak"] = 1
            self.l.info("Streak started for user.")
        else:
            last_day = last // 86400
            new_day = timestamp // 86400

            if new_day == last_day:
                self.l.debug("Activity occurred on the same day. Streak unchanged.")
                return
            if new_day - last_day == 1:
                user_data["streak"] += 1
                self.l.info(f"Streak incremented to {user_data['streak']}.")
            else:
                user_data["streak"] = 1
                self.l.info("Streak reset to 1.")

        user_data["last_activity_at"] = timestamp
        self.l.debug(f"Updated last_activity_at to {timestamp}.")

    async def _fetch_batch_activity(self, usernames: JSON) -> JSON | None:
        self.l.debug(f"Fetching batch activity for users: {usernames}...")

        user_parts = []

        for alias, name in usernames.items():
            user_parts.append(f"""
                {alias}: User(name: "{name}") {{
                    id
                    name
                }}
            """)

        query = f"query {{ {' '.join(user_parts)} }}"
        users_data = await self._query_graphql(query)

        if not users_data:
            self.l.warning("No user data returned from AniList API for batch activity.")
            return None

        id_map = {alias: payload["id"] for alias, payload in users_data.items() if payload}

        if not id_map:
            self.l.warning("No valid user IDs found in AniList API response.")
            return None

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
        return await self._query_graphql(query)

    async def _run_update_cycle(self) -> None:
        self.l.info("Starting AniList update cycle...")

        data = self._load_data()

        if not data["users"]:
            self.l.info("No users linked for AniList tracking. Skipping update cycle...")
            return

        channel_id = data.get("channel_id")
        channel = self.bot.get_channel(channel_id) if channel_id else None

        if not channel or not SimpleUtils.is_messageable(channel):
            self.l.warning("AniList notification channel is not set or not messageable. Skipping update...")
            return

        active_users = {}
        alias_to_discord = {}

        for i, (discord_id, u) in enumerate(data["users"].items()):
            alias = f"user_{i}"
            active_users[alias] = u["anilist"]
            alias_to_discord[alias] = discord_id

        batch = await self._fetch_batch_activity(active_users)

        if not batch:
            self.l.warning("No activity data returned from AniList API batch query.")
            return

        for alias, activity in batch.items():
            if not activity:
                self.l.debug(f"No activity found for alias {alias}.")
                continue

            discord_id = alias_to_discord[alias]
            user_data = data["users"][discord_id]
            activity_id = activity["id"]
            last_seen = user_data.get("last_activity_id", None)

            if not user_data.get("synced"):
                self.l.info(f"Syncing user data for member {discord_id}.")
                user_data["last_activity_id"] = activity_id
                user_data["synced"] = True

            if last_seen and activity_id <= last_seen:
                self.l.debug(f"No new activity for member {discord_id}. Last seen: {last_seen}.")
                continue

            self.l.info(user_data)
            self.l.info(activity)

            is_progress = self._is_real_progress(user_data, activity)
            user_data["last_activity_id"] = activity_id

            if not is_progress:
                self.l.debug(f"Activity for member {discord_id} is not real progress.")
                self.l.debug("Skipping streak update and notification...")

                continue

            self._update_streak(user_data, activity["createdAt"])

            embed = Embed(
                color=Color.ash_theme(),
                title=activity["media"]["title"]["romaji"],
                description=(
                    f"{activity['status'].title()}: **{self._extract_progress(activity)}**\n"
                    f"Current Streak: **{user_data['streak']}** {'days' if user_data['streak'] != 1 else 'day'}\n\n"
                    f"[**AniList**](https://anilist.co/anime/{activity['media']['id']}) | "
                    f"[**MyAnimeList**](https://myanimelist.net/anime/{activity['media']['idMal']})\n\n"
                    f"<t:{activity['createdAt']}:R>"
                ),
            )

            user = await self.bot.fetch_user(int(discord_id))

            embed.set_author(
                name=f"{activity['user']['name']} (@{user.name})" if user else activity["user"]["name"],
                url=f"https://anilist.co/user/{activity['user']['id']}",
                icon_url=activity["user"]["avatar"]["medium"],
            )

            self.l.info(f"Sending AniList update embed for member {discord_id}...")

            old_message_id = user_data.get("last_message_id")

            if old_message_id:
                try:
                    await channel.get_partial_message(old_message_id).delete()
                    self.l.debug(f"Deleted previous AniList message for {discord_id}.")

                except Exception:
                    self.l.debug("Previous message already deleted or inaccessible.")

            new_message = await channel.send(embed=embed)
            user_data["last_message_id"] = new_message.id

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)
        self.l.info("AniList update cycle completed and data saved.")

    @tasks.loop(seconds=Config.ANILIST_NORMAL_UPDATE_TIME_SEC)
    async def _check_updates(self) -> None:
        self.l.debug("Normal update loop triggered.")

        await self.bot.wait_until_ready()
        await self._run_update_cycle()

    @tasks.loop(seconds=Config.ANILIST_DEBUG_UPDATE_TIME_SEC)
    async def _debug_updates(self) -> None:
        self.l.debug("Debug update loop triggered.")

        await self.bot.wait_until_ready()
        await self._run_update_cycle()

    @group.command(name="force", description="Manually force an AniList update check for all linked users.")
    @commands.is_owner()
    async def anilist_force(self, interaction: Interaction) -> None:
        self.l.info("Manual AniList update forced by owner.")

        await interaction.response.defer(ephemeral=True)
        await self._run_update_cycle()
        await interaction.followup.send("Manual update executed.", ephemeral=True)

    @group.command(name="channel", description="Set the channel where AniList activity updates will be posted.")
    @commands.is_owner()
    @app_commands.describe(channel="The text channel that will receive AniList update notifications.")
    async def anilist_channel(self, interaction: Interaction, channel: TextChannel) -> None:
        self.l.info(f"Setting AniList notification channel to {channel.id}...")

        data = self._load_data()
        data["channel_id"] = channel.id

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)
        self.l.info(f"AniList notification channel set to {channel.id} and saved.")

        await interaction.response.send_message(
            f"Set {channel.mention} as AniList notification channel.",
            ephemeral=True,
        )

    @group.command(name="link", description="Link a Discord user to their AniList account for activity tracking.")
    @commands.is_owner()
    @app_commands.describe(
        member="The Discord member whose AniList account will be linked.",
        username="The AniList username to track (case-sensitive).",
    )
    async def anilist_link(self, interaction: Interaction, member: Member, username: str) -> None:
        self.l.info(f"Linking Discord user {member.id} to AniList username '{username}'.")

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
        self.l.info(f"Linked Discord user {member.id} to AniList username '{username}' and saved.")

        await interaction.followup.send(
            f"Linked {member.mention} to [{username}](<https://anilist.co/user/{username}>).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AniListCog(bot))

import aiohttp
import json
import logging

import discord
import discord.ext.commands
import discord.ext.tasks

from chii.config import Config
from chii.utils import JSON, SimpleUtils

class AniListCog(discord.ext.commands.Cog):
    l = logging.getLogger(f"chii.cogs.{__qualname__}")
    group = discord.app_commands.Group(name="anilist", description="AniList tracking commands.")

    def __init__(self, bot: discord.ext.commands.Bot) -> None:
        self.bot = bot
        self.session = aiohttp.ClientSession()

        self.check_updates.start()
        self.l.info("AniListCog initialized.")

    async def cog_unload(self) -> None:
        self.check_updates.cancel()

        if self.debug_updates.is_running():
            self.debug_updates.cancel()

        await self.session.close()

    def _load_data(self) -> JSON:
        default_data = {
            "channel_id": None,
            "users": {}
        }

        if not Config.ANILIST_DATA_PATH.exists():
            SimpleUtils.save_data(Config.ANILIST_DATA_PATH, default_data)
            return default_data.copy()

        with open(Config.ANILIST_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    async def _query_graphql(self, query: str, variables: JSON | None = None) -> JSON | None:
        api_url = "https://graphql.anilist.co"
        payload = {
            "url": api_url,
            "json": {
                "query": query,
                "variables": variables or {}
            }
        }

        try:
            async with self.session.post(**payload) as response:
                if response.status != 200:
                    text = await response.text()
                    self.l.error(f"AniList API Error {response.status}: {text}")
                    return None

                data = await response.json()

                if "errors" in data:
                    self.l.error(f"AniList GraphQL Error: {data["errors"]}")
                    return None

                self.l.info("Retrieved data from AniList.")

                return data.get("data")

        except Exception as e:
            self.l.warning(f"AniList API Error: {e}")
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
            self.l.info(f"Ignoring non-consumption activity \"{status}\".")
            return False

        return True

    def _extract_progress(self, activity: JSON) -> int | None:
        raw = activity.get("progress")

        if raw is None:
            return None

        try:
            text = str(raw).strip()

            if "-" in text:
                text = text.split("-")[-1].strip()

            return int(text)

        except (ValueError, TypeError):
            return None

    def _is_real_progress(self, user_data: JSON, activity: JSON) -> bool:
        if not self._is_consumption_activity(activity):
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
            return False

        if new_progress > old_progress:
            self.l.info(f"Progress of media {media_id} increased from {old_progress} to {new_progress}.")
            return True

        return False

    def _update_streak(self, user_data: JSON, timestamp: int) -> None:
        last = user_data.get("last_activity_at", 0)

        if last == 0:
            user_data["streak"] = 1
        else:
            last_day = last // 86400
            new_day = timestamp // 86400

            if new_day == last_day:
                return
            elif new_day - last_day == 1:
                user_data["streak"] += 1
            else:
                user_data["streak"] = 1

        user_data["last_activity_at"] = timestamp

    async def _fetch_batch_activity(self, usernames: JSON) -> JSON | None:
        user_parts = []

        for alias, name in usernames.items():
            user_parts.append(f"""
                {alias}: User(name: "{name}") {{
                    id
                    name
                }}
            """)

        query = f"query {{ {" ".join(user_parts)} }}"
        users_data = await self._query_graphql(query)

        if not users_data:
            return None

        id_map = {
            alias: payload["id"]
                for alias, payload
                in users_data.items()
                if payload
        }

        if not id_map:
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

        query = f"query {{ {" ".join(activity_parts)} }}"

        return await self._query_graphql(query)

    async def _run_update_cycle(self) -> None:
        data = self._load_data()

        if not data["users"]:
            return

        channel_id = data.get("channel_id")
        channel = self.bot.get_channel(channel_id) if channel_id else None

        if not channel or not SimpleUtils.is_messageable(channel):
            return

        active_users = {}
        alias_to_discord = {}

        for i, (discord_id, u) in enumerate(data["users"].items()):
            alias = f"user_{i}"
            active_users[alias] = u["anilist"]
            alias_to_discord[alias] = discord_id

        batch = await self._fetch_batch_activity(active_users)

        if not batch:
            return

        for alias, activity in batch.items():
            if not activity:
                continue

            discord_id = alias_to_discord[alias]
            user_data = data["users"][discord_id]

            activity_id = activity["id"]
            last_seen = user_data.get("last_activity_id", 0)

            if not user_data.get("synced"):
                self.l.info("Syncing user data...")

                user_data["last_activity_id"] = activity_id
                user_data["synced"] = True

                continue

            if activity_id <= last_seen:
                continue

            is_progress = self._is_real_progress(user_data, activity)
            user_data["last_activity_id"] = activity_id

            if not is_progress:
                continue

            self._update_streak(user_data, activity["createdAt"])

            user = await self.bot.fetch_user(int(discord_id))
            progress = self._extract_progress(activity)

            embed = discord.Embed(
                color=discord.Color.ash_theme(),
                title=activity["media"]["title"]["romaji"],
                description=(
                    f"{activity["status"].title()}: **{progress}**\n"
                    f"Current Streak: **{user_data["streak"]}** "
                    f"{"days" if user_data["streak"] != 1 else "day"}\n\n"
                    f"[**AniList**](https://anilist.co/anime/{activity["media"]["id"]}) | "
                    f"[**MyAnimeList**](https://myanimelist.net/anime/{activity["media"]["idMal"]})"
                ),
            )

            embed.set_author(
                name=f"{activity["user"]["name"]} (@{user.name})" if user else activity["user"]["name"],
                url=f"https://anilist.co/user/{activity["user"]["id"]}",
                icon_url=activity["user"]["avatar"]["medium"],
            )

            await channel.send(embed=embed) # type: ignore

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)

    @discord.ext.tasks.loop(seconds=Config.ANILIST_NORMAL_UPDATE_TIME_S)
    async def check_updates(self):
        await self.bot.wait_until_ready()
        await self._run_update_cycle()

    @discord.ext.tasks.loop(seconds=Config.ANILIST_DEBUG_UPDATE_TIME_S)
    async def debug_updates(self):
        await self.bot.wait_until_ready()
        await self._run_update_cycle()

    @group.command(name="force")
    @discord.ext.commands.is_owner()
    async def force_update(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._run_update_cycle()
        await interaction.followup.send("Manual update executed.", ephemeral=True)

    @group.command(name="channel")
    @discord.ext.commands.is_owner()
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        data = self._load_data()
        data["channel_id"] = channel.id
        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)

        await interaction.response.send_message(f"Set {channel.mention} as AniList notification channel.", ephemeral=True)

    @group.command(name="link")
    @discord.ext.commands.is_owner()
    async def link(self, interaction: discord.Interaction, member: discord.Member, username: str):
        await interaction.response.defer(ephemeral=True)

        data = self._load_data()
        data["users"][str(member.id)] = {
            "anilist": username,
            "streak": 0,
            "last_activity_at": 0,
            "progress_cache": {},
            "last_activity_id": 0,
            "synced": False,
        }

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)

        await interaction.followup.send(f"Linked {member.mention} to https://anilist.co/user/{username}", ephemeral=True)

async def setup(bot: discord.ext.commands.Bot) -> None:
    await bot.add_cog(AniListCog(bot))

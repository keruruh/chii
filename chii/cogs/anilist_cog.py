import aiohttp
import json
import logging

import discord
import discord.ext.commands
import discord.ext.tasks

from chii.config import Config
from chii.utils import SimpleUtils

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

    def _load_data(self) -> SimpleUtils.JSON:
        default_data = {
            "channel_id": None,
            "users": {}
        }

        if not Config.ANILIST_DATA_PATH.exists():
            SimpleUtils.save_data(Config.ANILIST_DATA_PATH, default_data)
            return default_data.copy()

        with open(Config.ANILIST_DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    async def _query_graphql(self, query: str, variables: SimpleUtils.JSON | None = None) -> SimpleUtils.JSON | None:
        api_url = "https://graphql.anilist.co"
        payload = {
            "query": query,
            "variables": variables or {}
        }

        try:
            async with self.session.post(url=api_url, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    self.l.error(f"AniList API Error {response.status}: {text}")

                    return None

                data = await response.json()
                self.l.info("Retrieved data from AniList.")

                if "errors" in data:
                    self.l.error(f"AniList GraphQL Error: {data["errors"]}")
                    return None

                return data.get("data")

        except Exception as e:
            self.l.warning(f"AniList API Error: {e}")
            return None

    async def _validate_user(self, username: str) -> bool:
        query = """
            query ($name: String) {
                User(name: $name) {
                    id
                    name

                    options { profileColor }
                    mediaListOptions { scoreFormat }
                }
            }
        """

        data = await self._query_graphql(query, { "name": username })
        self.l.info(f"Validating data for AniList {username}...")

        return data is not None and data.get("User") is not None

    async def _user_is_active(self, username: str) -> bool:
        query = """
            query ($name: String) {
                anime: MediaListCollection(userName: $name, type: ANIME, status_in: [CURRENT, REPEATING]) { lists { name } }
                manga: MediaListCollection(userName: $name, type: MANGA, status_in: [CURRENT, REPEATING]) { lists { name } }
            }
        """

        data = await self._query_graphql(query, { "name": username })
        self.l.info(f"Checking if user {username} is active...")

        if not data:
            return False

        anime_lists = data["anime"]["lists"] if data.get("anime") else []
        manga_lists = data["manga"]["lists"] if data.get("manga") else []

        return bool(anime_lists or manga_lists)

    async def _fetch_batch_activity(self, usernames: SimpleUtils.JSON) -> SimpleUtils.JSON | None:
        user_parts = []

        for alias, name in usernames.items():
            user_parts.append(f"""
                {alias}: User(name: "{name}") {{
                    id
                    name
                }}
            """)

        user_query = f"query {{ {" ".join(user_parts)} }}"
        users_data = await self._query_graphql(user_query)

        if not users_data:
            self.l.info("Failed resolving AniList user IDs.")
            return None

        id_map = {}

        for alias, payload in users_data.items():
            if payload:
                id_map[alias] = payload["id"]

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
                            type

                            title {{
                                native
                                romaji
                            }}
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
        data = await self._query_graphql(query)

        return data

    def _get_int_progress(self, progress_string) -> int | None:
        try:
            if "-" in progress_string:
                return int(progress_string.split("-")[-1].strip())
            else:
                return int(progress_string)

        except ValueError:
            self.l.info(f"Unexpected error while parsing progress \"{progress_string}\".")
            return

    def _is_real_progress(self, user_data: SimpleUtils.JSON, activity: SimpleUtils.JSON) -> bool:
        media_id = str(activity["media"]["id"])
        progress = str(activity.get("progress", "")).strip()

        self.l.info(f"Checking progress for media {media_id} with activity of \"{progress}\"...")

        if not progress:
            self.l.info("No progress found.")
            return False

        new_progress = self._get_int_progress(progress)
        cache = user_data.setdefault("progress_cache", {})
        old = cache.get(media_id)

        cache[media_id] = new_progress

        if old is None:
            self.l.info(f"No cache found for media {media_id}.")
            return False

        if new_progress > old:
            self.l.info(f"Valid progress found for media {media_id}.")
            return True

        self.l.info(f"No valid progress was found for media {media_id}.")

        return False

    def _update_streak(self, user_data: SimpleUtils.JSON, timestamp: int) -> None:
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

    async def _run_update_cycle(self) -> None:
        data = self._load_data()

        if not data["users"]:
            self.l.info("No users were found.")
            return

        channel_id = data.get("channel_id")
        channel = self.bot.get_channel(channel_id) if channel_id else None

        if not channel:
            self.l.info("No notify channel was set.")
            return

        active_users = {}
        alias_to_discord = {}

        self.l.info("Enumerating users...")

        for i, (discord_id, u) in enumerate(data["users"].items()):
            self.l.info(f"Found {u["anilist"]} with Discord ID {discord_id}.")

            if await self._user_is_active(u["anilist"]):
                alias = f"user_{i}"
                active_users[alias] = u["anilist"]
                alias_to_discord[alias] = discord_id

        if not active_users:
            self.l.info("No active users. Skipping poll...")
            return

        batch = await self._fetch_batch_activity(active_users)

        if not batch:
            self.l.info("No activity was found.")
            return

        for alias, activity in batch.items():
            if not activity:
                continue

            discord_id = alias_to_discord[alias]
            user_data = data["users"][discord_id]

            activity_id = str(activity["id"])

            if not user_data.get("synced"):
                self.l.info("Syncing user data...")

                self._is_real_progress(user_data, activity)
                user_data.setdefault("history", []).append(activity_id)
                user_data["synced"] = True

                continue

            history = user_data.setdefault("history", [])

            if activity_id in history:
                self.l.info(f"Activity {activity_id} found in history. Skipping...")
                continue

            if not self._is_real_progress(user_data, activity):
                self.l.info("Activity was not real progress. Skipping...")

                history.append(activity_id)
                history[:] = history[-50:]

                continue

            history.append(activity_id)
            history[:] = history[-50:]

            self._update_streak(user_data, activity["createdAt"])

            user = await self.bot.fetch_user(int(discord_id))

            embed_title = activity["media"]["title"]["romaji"]
            embed_description = (
                f"{activity["status"].title()}: **{self._get_int_progress(activity["progress"])}**\n"
                f"Current Streak: **{user_data["streak"]}** {"days" if user_data["streak"] != 1 else "day"}\n\n"
                f"[**AniList**](https://anilist.co/anime/{activity["media"]["id"]}) | [**MyAnimeList**](https://myanimelist.net/anime/{activity["media"]["idMal"]})"
            )

            embed = discord.Embed(title=embed_title, description=embed_description, color=discord.Color.ash_theme())

            embed.set_author(
                name=f"{activity["user"]["name"]} {"(@" + user.name + ")" if user else None}",
                url=f"https://anilist.co/user/{activity["user"]["id"]}",
                icon_url=activity["user"]["avatar"]["medium"] if activity["user"]["avatar"] else None,
            )

            if not SimpleUtils.is_messageable(channel):
                self.l.warning(f"Channel {channel.id} is not messageable.")
                return
            else:
                await channel.send(embed=embed) # type: ignore

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)

    @discord.ext.tasks.loop(seconds=Config.ANILIST_NORMAL_UPDATE_TIME_S)
    async def check_updates(self) -> None:
        await self.bot.wait_until_ready()
        self.l.info("Running scheduled updates...")
        await self._run_update_cycle()

    @discord.ext.tasks.loop(seconds=Config.ANILIST_DEBUG_UPDATE_TIME_S)
    async def debug_updates(self) -> None:
        await self.bot.wait_until_ready()
        self.l.info("Running debug update cycle...")
        await self._run_update_cycle()

    @group.command(name="force", description="Force an update for all users.")
    @discord.ext.commands.is_owner()
    async def force_update(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._run_update_cycle()
        await interaction.followup.send("Manual update executed.", ephemeral=True)

    @group.command(name="debug", description="Enable or disable debug updates.")
    @discord.app_commands.describe(enabled="Whether to enable or disable debug updates.")
    @discord.ext.commands.is_owner()
    async def debug_toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        if enabled:
            if not self.debug_updates.is_running():
                self.debug_updates.start()

            await interaction.response.send_message("Debug polling enabled.", ephemeral=True)
        else:
            if self.debug_updates.is_running():
                self.debug_updates.cancel()

            await interaction.response.send_message("Debug polling disabled.", ephemeral=True)

    @group.command(name="channel", description="Set the channel used to announce updates.")
    @discord.app_commands.describe(channel="The actual channel.")
    @discord.ext.commands.is_owner()
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        data = self._load_data()
        data["channel_id"] = channel.id
        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)

        await interaction.response.send_message(f"Set {channel.mention} as AniList notify channel.", ephemeral=True)

    @group.command(name="link", description="Link a Discord user to their AniList account.")
    @discord.app_commands.describe(member="The Discord user.", username="The AniList username.")
    @discord.ext.commands.is_owner()
    async def link(self, interaction: discord.Interaction, member: discord.Member, username: str) -> None:
        await interaction.response.defer(ephemeral=True)

        valid = await self._validate_user(username)

        if not valid:
            await interaction.followup.send("AniList user was not found or their profile is private.", ephemeral=True)
            return

        data = self._load_data()

        data["users"][str(member.id)] = {
            "anilist": username,
            "streak": 0,
            "last_activity_at": 0,
            "history": [],
            "progress_cache": {},
            "synced": False,
        }

        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)

        await interaction.followup.send(f"Successfully linked {member.mention} to [{username}](<https://anilist.co/user/{username}>).", ephemeral=True)

    @group.command(name="unlink", description="Unlink a user.")
    @discord.app_commands.describe(member="The user to unlink.")
    @discord.ext.commands.is_owner()
    async def unlink(self, interaction: discord.Interaction, member: discord.Member) -> None:
        data = self._load_data()
        data["users"].pop(str(member.id), None)
        SimpleUtils.save_data(Config.ANILIST_DATA_PATH, data)

        await interaction.response.send_message(f"Unlinked {member.mention}.", ephemeral=True)

async def setup(bot: discord.ext.commands.Bot) -> None:
    await bot.add_cog(AniListCog(bot))

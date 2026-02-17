import asyncio
import logging
import pathlib
import subprocess
import typing
import uuid

import discord
import discord.ext.commands

import yt_dlp

from chii.config import Config

class VideoJob(typing.TypedDict):
    message: discord.Message
    url: str

class VideoWorker:
    l = logging.getLogger(f"chii.utils.{__qualname__}")

    def __init__(self: typing.Self, bot: discord.ext.commands.Bot, worker_count: int, max_queue_size: int) -> None:
        self.bot = bot

        self.queue = asyncio.Queue(max_queue_size)
        self.active_urls = set()
        self.worker_count = worker_count

        self.l.info(f"VideoWorker initialized with {worker_count} workers and a max queue size of {max_queue_size}.")

    def _download_video(self: typing.Self, url: str) -> pathlib.Path | None:
        Config.REPOSTS_TEMP_DIR.mkdir(parents=True, exist_ok=True)

        filename = f"{uuid.uuid4()}.mp4"
        output = Config.REPOSTS_TEMP_DIR / filename

        class _YTDLogger:
            _l = VideoWorker.l

            def debug(self: typing.Self, msg: str) -> None:
                self._l.debug(f"[YT_DLP]: {msg}")

            def warning(self: typing.Self, msg: str) -> None:
                self._l.warning(f"[YT_DLP]: {msg}")

            def error(self: typing.Self, msg: str) -> None:
                self._l.error(f"[YT_DLP]: {msg}")

        options = {
            # Has to be a string since yt-dlp works with os.
            "outtmpl": str(output),

            "format": "mp4/bestvideo+bestaudio/best",

            "quiet": True,
            "noplaylist": True,
            "cookiefile": None,

            "logger": _YTDLogger(),
        }

        try:
            with yt_dlp.YoutubeDL(options) as yt: # type: ignore
                yt.download([url])

            self.l.info(f"Downloaded video from {url} to {output}.")

        except Exception as e:
            self.l.error(f"Failed to download video from {url}: {e}")
            return None

        return output if output.exists() else None

    def _get_duration(self: typing.Self, path: pathlib.Path) -> float:
        command = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]

        result = subprocess.run(command, capture_output=True, text=True)

        try:
            duration = float(result.stdout.strip())
            self.l.info(f"Got duration {duration}s for file {path}.")

            return duration

        except Exception as e:
            self.l.error(f"Failed to get duration for {path}: {e}")
            raise

    def _compress_to_limit(self: typing.Self, input_file: pathlib.Path) -> pathlib.Path | None:
        duration = self._get_duration(input_file)
        max_bytes = Config.REPOSTS_MAX_SIZE_MB * 1024 * 1024
        bitrate = int(((max_bytes * 8) / duration) / 1000)

        output = Config.REPOSTS_TEMP_DIR / f"{uuid.uuid4()}_compressed.mp4"

        command = [
            "ffmpeg",
            "-y",
            "-i", input_file,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-b:v", f"{bitrate}k",
            "-maxrate", f"{bitrate}k",
            "-bufsize", f"{bitrate}k",
            "-c:a", "aac",
            output,
        ]

        self.l.info(f"Compressing {input_file} to {output} with bitrate {bitrate}k...")
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not output.exists():
            self.l.error(f"Compression failed. The {output} file was not created.")
            return None

        if output.stat().st_size > max_bytes:
            self.l.error(f"Compressed file {output} exceeds maximum size of {max_bytes} bytes.")
            return None

        self.l.info(f"Compressed video saved to {output}.")
        return output

    async def _process_job(self: typing.Self, job: VideoJob, worker_id: int) -> None:
        message = job["message"]
        url = job["url"]

        self.l.info(f"[Video Worker {worker_id}]: Processing job for URL {url}...")

        async with message.channel.typing():
            loop = asyncio.get_running_loop()

            video = await loop.run_in_executor(None, self._download_video, url)

            if not video:
                self.l.error(f"[Video Worker {worker_id}]: Failed to download video from {url}.")
                return

            compressed = await loop.run_in_executor(None, self._compress_to_limit, video)

            video.unlink(missing_ok=True)
            self.l.info(f"[Video Worker {worker_id}]: Removed original video file {video}.")

            if not compressed:
                self.l.error(f"[Video Worker {worker_id}: Failed to compress video from {url}.")
                return

        user_text = message.content.replace(url, "").strip()

        member = message.guild.get_member(message.author.id) if message.guild else None
        nick = member.nick if member and member.nick else message.author.display_name
        username = message.author.name

        repost_text = (
            f"{user_text}\n\n"
            f"-# Sent: **@{username}** ({nick})\n"
            f"-# Source: **<{url}>**"
        )

        try:
            await message.delete()
            self.l.info(f"[Video Worker {worker_id}]: Deleted original message from user {message.author.id}.")

        except Exception as e:
            self.l.warning(f"[Video Worker {worker_id}]: Could not delete message: {e}")

        await message.channel.send(repost_text, file=discord.File(compressed))
        self.l.info(f"[Video Worker {worker_id}]: Sent reposted video to channel {message.channel.id}.")

        compressed.unlink(missing_ok=True)
        self.l.info(f"[Video Worker {worker_id}]: Removed compressed video file {compressed}.")

    async def _worker_loop(self: typing.Self, worker_id: int) -> None:
        self.l.info(f"[Video Worker {worker_id}]: Ready.")

        while True:
            job = await self.queue.get()

            try:
                await self._process_job(job, worker_id)
            except Exception as e:
                self.l.error(f"[Video Worker {worker_id}]: Error: {e}")
            finally:
                self.active_urls.discard(job["url"])
                self.queue.task_done()

    async def enqueue(self: typing.Self, job: VideoJob) -> None:
        url = job["url"]

        if url in self.active_urls:
            self.l.info(f"The URL {url} is already in queue. Skipping...")
            return

        if self.queue.full():
            self.l.warning("Queue is full. Skipping job...")
            return

        self.active_urls.add(url)
        self.l.info(f"Enqueued job for URL {url}. Queue size is now {self.queue.qsize()}.")

        await self.queue.put(job)

    def start(self) -> None:
        for i in range(self.worker_count):
            asyncio.create_task(self._worker_loop(i))

        self.l.info(f"Started {self.worker_count} video workers.")

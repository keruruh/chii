import asyncio
import logging
import pathlib
import subprocess
import typing as t
import uuid

import yt_dlp
from discord import File, Message
from discord.ext import commands

from chii.config import Config
from chii.utils import T_NUMERIC


class _YTDLogger:
    def debug(self: t.Self, _msg: str) -> None:
        pass

    def warning(self: t.Self, _msg: str) -> None:
        pass

    def error(self: t.Self, _msg: str) -> None:
        pass


class _VideoJob(t.TypedDict):
    message: Message
    url: str


class VideoWorker:
    l = logging.getLogger(f"chii.utils.{__qualname__}")

    def __init__(self: t.Self, bot: commands.Bot, worker_count: int, max_queue_size: int) -> None:
        self.bot = bot

        self.queue = asyncio.Queue(max_queue_size)
        self.worker_count = worker_count
        self.active_urls = set()
        self.tasks = []

        self.l.info(f"VideoWorker initialized with {worker_count} workers and a max queue size of {max_queue_size}.")

    def start(self) -> None:
        self.l.info("Starting video worker threads...")

        for i in range(self.worker_count):
            self.tasks.append(asyncio.create_task(self._worker_loop(i)))

        self.l.info(f"Started {self.worker_count} video workers.")

    async def stop(self) -> None:
        self.l.info("Stopping all video worker tasks...")

        for task in self.tasks:
            task.cancel()

        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.l.info("All video worker tasks stopped.")

    async def enqueue(self: t.Self, job: _VideoJob) -> None:
        url = job["url"]

        if url in self.active_urls:
            self.l.info(f'The URL "{url}" is already in queue. Skipping...')
            return

        if self.queue.full():
            self.l.warning("Queue is full! Skipping job...")
            return

        self.active_urls.add(url)
        self.l.info(f'Enqueued job for URL "{url}". Queue size is now {self.queue.qsize()}.')

        await self.queue.put(job)

    def _download_video(self: t.Self, url: str) -> pathlib.Path | None:
        self.l.info(f'Starting download for video URL "{url}"...')

        Config.TEMP_PATH.mkdir(parents=True, exist_ok=True)

        filename = f"{uuid.uuid4()}.mp4"
        output = Config.TEMP_PATH / filename

        options = {
            # Has to be a string since yt-dlp works with os module.
            "outtmpl": str(output),
            # Prioritize low quality for uploads.
            "format": "mp4/bestvideo[height<=480]+bestaudio/best[height<=480]",
            "quiet": True,
            "noplaylist": True,
            "cookiefile": None,
            "logger": _YTDLogger(),
        }

        try:
            with yt_dlp.YoutubeDL(**options) as yt:
                yt.download([url])

            self.l.info(f'Downloaded video from "{url}" to "{output}".')

        except Exception:
            self.l.exception(f'Failed to download video from "{url}"!')
            return None

        if not output.exists():
            self.l.error(f'Download completed but output file "{output}" does not exist.')
            return None

        return output

    def _get_duration(self: t.Self, path: pathlib.Path) -> float:
        self.l.debug(f"Getting duration for file {path}...")

        # fmt: off
        command = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        # fmt: on

        result = subprocess.run(command, capture_output=True, text=True)

        try:
            duration = float(result.stdout.strip())
            self.l.info(f'Got duration {duration}s for file "{path}".')

        except Exception:
            self.l.exception(f'Failed to get duration for "{path}"!')
            raise

        else:
            return duration

    async def _worker_loop(self: t.Self, worker_id: T_NUMERIC) -> None:
        self.l.info(f"[Video Worker {worker_id}]: Ready.")

        while True:
            job = await self.queue.get()

            self.l.debug(f'[Video Worker {worker_id}]: Picked up job for URL {job["url"]} from queue.')

            try:
                await self._process_job(job, worker_id)
            except Exception:
                self.l.exception(f"[Video Worker {worker_id}]: Unexpected exception while processinrg job!")
            finally:
                self.active_urls.discard(job["url"])
                self.l.debug(f'[Video Worker {worker_id}]: Job for URL {job["url"]} completed and removed from queue.')
                self.queue.task_done()

    async def _process_job(self: t.Self, job: _VideoJob, worker_id: T_NUMERIC) -> None:
        message = job["message"]
        url = job["url"]

        self.l.info(f'[Video Worker {worker_id}]: Processing job for URL "{url}"...')

        async with message.channel.typing():
            loop = asyncio.get_running_loop()

            video = await loop.run_in_executor(None, self._download_video, url)

            if not video:
                self.l.error(f'[Video Worker {worker_id}]: Failed to download video from URL "{url}".')
                return

            compressed = await loop.run_in_executor(None, self._compress_to_limit, video)

            video.unlink(missing_ok=True)
            self.l.info(f"[Video Worker {worker_id}]: Removed original video file {video}.")

            if not compressed:
                self.l.error(f'[Video Worker {worker_id}]: Failed to compress video from URL "{url}".')
                return

        user_text = message.content.replace(url, "").strip()
        member = message.guild.get_member(message.author.id) if message.guild else None
        nick = member.nick if member and member.nick else message.author.display_name
        username = message.author.name

        repost_text = f"{user_text}\n\n-# Sent: **@{username}** ({nick})\n-# Source: **<{url}>**"

        try:
            await message.delete()
            self.l.info(f"[Video Worker {worker_id}]: Deleted original message from user {message.author.id}.")

        except Exception:
            self.l.exception(f"[Video Worker {worker_id}]: Could not delete message!")

        await message.channel.send(repost_text, file=File(compressed))
        self.l.info(f"[Video Worker {worker_id}]: Sent reposted video to channel {message.channel.id}.")

        compressed.unlink(missing_ok=True)
        self.l.info(f"[Video Worker {worker_id}]: Removed compressed video file {compressed}.")

    def _compress_to_limit(self: t.Self, input_file: pathlib.Path) -> pathlib.Path | None:
        self.l.info(f'Starting compression for input file "{input_file}"...')

        duration = self._get_duration(input_file)
        max_bytes = Config.REPOSTS_MAX_SIZE_MB * 1024 * 1024
        bitrate = int(((max_bytes * 8) / duration) / 1000)

        output = Config.TEMP_PATH / f"{uuid.uuid4()}_compressed.mp4"

        # fmt: off
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
        # fmt: on

        self.l.info(f'Compressing input file "{input_file}" to "{output}" with bitrate {bitrate}k...')
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not output.exists():
            self.l.error(f'Compression failed. The "{output}" file was not created.')
            return None

        if output.stat().st_size > max_bytes + (1 * 1024 * 1024):
            self.l.error(f'Compressed file "{output}" exceeds maximum size of {max_bytes} bytes.')
            return None

        self.l.info(f'Compressed video saved to "{output}".')
        return output

import logging
from logging.handlers import RotatingFileHandler

from chii.config import Config


class LogHandler:
    l = logging.getLogger(f"chii.utils.{__qualname__}")

    @classmethod
    def setup(cls) -> None:
        Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

        formatter = logging.Formatter(Config.LOGS_FORMAT)
        root_logger = logging.getLogger()

        if root_logger.handlers:
            return

        main_handler = RotatingFileHandler(
            filename=Config.LOGS_DIR / "chii.log",
            maxBytes=Config.LOGS_MAX_SIZE_MB * 1024 * 1024,
            backupCount=Config.LOGS_BACKUP_COUNT,
            encoding="utf-8",
        )

        main_handler.setFormatter(formatter)

        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(main_handler)

        discord_handler = RotatingFileHandler(
            filename=Config.LOGS_DIR / "discord.log",
            maxBytes=Config.LOGS_MAX_SIZE_MB * 1024 * 1024,
            backupCount=Config.LOGS_BACKUP_COUNT,
            encoding="utf-8",
        )

        discord_handler.setFormatter(formatter)

        discord_logger = logging.getLogger("discord")
        discord_logger.setLevel(logging.INFO)
        discord_logger.addHandler(discord_handler)

        # Prevent discord logs appearing in "chii.log".
        discord_logger.propagate = False

        logging.getLogger("discord.http").setLevel(logging.WARNING)

        cls.l.info("Logging system initialized.")

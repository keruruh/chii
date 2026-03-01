import os
import pathlib

import dotenv

dotenv.load_dotenv()


class Config:
    _ROOT_PATH = pathlib.Path(__file__).resolve().parent.parent / "chii"

    DATA_PATH = _ROOT_PATH / "data"
    LOGS_PATH = DATA_PATH / "logs"
    TEMP_PATH = DATA_PATH / "temp"

    ANILIST_DATA_PATH = DATA_PATH / "anilist.json"
    REMINDERS_DATA_PATH = DATA_PATH / "reminders.json"
    REPOSTS_DATA_PATH = DATA_PATH / "reposts.json"

    BOT_PREFIX = "!!"
    BOT_TOKEN = str(os.getenv("BOT_TOKEN", "0"))
    BOT_OWNER = int(os.getenv("BOT_OWNER", "0"))

    ANILIST_NORMAL_UPDATE_TIME_SEC = float(10 * 60)
    ANILIST_DEBUG_UPDATE_TIME_SEC = float(10)

    REMINDERS_MAX_COUNT = 10
    REMINDERS_MAX_MESSAGE_LEN = 100
    REMINDERS_MIN_TIME_SEC = float(10)

    REPOSTS_URL_REGEX = r"(https?://(?:www\.)?(?:tiktok\.com|instagram\.com)/[^\s]+)"

    # Recommended to set it to ~1 MB less than the current Discord server's limit.
    REPOSTS_MAX_SIZE_MB = 7

    LOGS_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    LOGS_BACKUP_COUNT = 5
    LOGS_MAX_SIZE_MB = 10

import os
import pathlib

import dotenv

dotenv.load_dotenv()

class Config:
    _ROOT_PATH = pathlib.Path(__file__).resolve().parent.parent / "chii"

    BOT_PREFIX = "!!"
    BOT_TOKEN = str(os.getenv("BOT_TOKEN", "0"))
    BOT_OWNER = int(os.getenv("BOT_OWNER", "0"))

    ANILIST_DATA_PATH = _ROOT_PATH / "data" / "anilist.json"
    ANILIST_NORMAL_UPDATE_TIME_S = float(60 * 60)
    ANILIST_DEBUG_UPDATE_TIME_S = float(10)

    REMINDERS_DATA_PATH = _ROOT_PATH / "data" / "reminders.json"
    REMINDERS_MAX_COUNT = 1

    REPOSTS_DATA_PATH = _ROOT_PATH / "data" / "reposts.json"
    REPOSTS_TEMP_DIR = _ROOT_PATH / "data" / "temp"
    REPOSTS_URL_REGEX = r"(https?://(?:www\.)?(?:tiktok\.com|instagram\.com)/[^\s]+)"
    REPOSTS_MAX_SIZE_MB = 8

    LOGS_DIR = _ROOT_PATH / "data" / "logs"
    LOGS_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    LOGS_MAX_SIZE_MB = 10
    LOGS_BACKUP_COUNT = 5

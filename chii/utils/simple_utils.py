import json
import logging
import pathlib
import typing as t

from discord.abc import GuildChannel, Messageable

from chii.utils import T_DATA


class SimpleUtils:
    l = logging.getLogger(f"chii.utils.{__qualname__}")

    @classmethod
    def save_data(cls, path: pathlib.Path, data: T_DATA | list[t.Any]) -> None:
        try:
            with pathlib.Path(path).open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

        except Exception:
            cls.l.exception("Failed saving reminders.")

    @staticmethod
    def is_guild_messageable(channel: t.Any, /) -> t.TypeGuard[Messageable]:
        return isinstance(channel, GuildChannel) and isinstance(channel, Messageable)

    @staticmethod
    def paginate_text(text: str, /) -> list[str]:
        pages = []
        buffer = ""
        max_page_size = 1800

        for line in text.splitlines(keepends=True):
            if len(buffer) + len(line) > max_page_size:
                pages.append(buffer)
                buffer = line

            else:
                buffer += line

        if buffer:
            pages.append(buffer)

        return pages

    @staticmethod
    def parse_time(time_string: str, /) -> float:
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
                raise ValueError("Invalid time format.")

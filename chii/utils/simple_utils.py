import json
import logging
import pathlib
import typing as t

from discord.abc import Messageable

type JSON = dict[str, t.Any]


class SimpleUtils:
    l = logging.getLogger(f"chii.utils.{__qualname__}")

    @classmethod
    def save_data(cls, path: pathlib.Path, data: JSON | list[t.Any]) -> None:
        try:
            with pathlib.Path(path).open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

        except Exception:
            cls.l.exception("Failed saving reminders.")

    @staticmethod
    def is_messageable(channel: t.Any, /) -> bool:
        return isinstance(channel, Messageable)

    @staticmethod
    def paginate_text(text: str) -> list[str]:
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

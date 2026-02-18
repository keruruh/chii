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

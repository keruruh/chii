import json
import logging
import pathlib
import typing

import discord

type JSON = dict[str, typing.Any]


class SimpleUtils:
    l = logging.getLogger(f"chii.utils.{__qualname__}")

    @classmethod
    def save_data(cls, path: pathlib.Path, data: JSON | list[typing.Any]) -> None:
        try:
            with pathlib.Path(path).open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

        except Exception:
            cls.l.exception("Failed saving reminders.")

    @staticmethod
    def is_messageable(channel: typing.Any, /) -> bool:
        return isinstance(channel, discord.abc.Messageable)

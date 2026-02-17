import json
import logging
import pathlib
import typing

import discord

class SimpleUtils:
    l = logging.getLogger(f"chii.utils.{__qualname__}")

    type JSON = dict[str, typing.Any]

    @classmethod
    def save_data(cls, path: pathlib.Path, data: SimpleUtils.JSON | list[typing.Any]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

        except Exception:
            cls.l.exception("Failed saving reminders.")

    @staticmethod
    def is_messageable(channel: typing.Any, /) -> bool:
        return isinstance(channel, discord.abc.Messageable)

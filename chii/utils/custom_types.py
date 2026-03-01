import typing as t

from discord import Thread
from discord.abc import GuildChannel, Messageable, PrivateChannel

type T_DATA = dict[str, t.Any]
type T_CHANNEL = GuildChannel | Messageable | PrivateChannel | Thread | None
type T_NUMERIC = int | str

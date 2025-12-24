from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord import Embed
    from discord.ui import View
    from matplotlib.figure import Figure
    from services.bot import DCSServerBot

__all__ = ["ReportEnv"]


@dataclass
class ReportEnv:
    bot: DCSServerBot
    embed: Embed = None
    view: View = None
    figure: Figure | None = None
    filename: str | None = None
    buffer: BytesIO = None
    params: dict = None
    mention: str = None
    report: str = None

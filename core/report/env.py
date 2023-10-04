from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import DCSServerBot
    from discord import Embed
    from discord.ui import View
    from matplotlib.figure import Figure

__all__ = ["ReportEnv"]


@dataclass
class ReportEnv:
    bot: DCSServerBot
    embed: Embed = None
    view: View = None
    figure: Figure = None
    filename: str = None
    params: dict = None

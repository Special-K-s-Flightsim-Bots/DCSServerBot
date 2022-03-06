import discord
from core import DCSServerBot
from dataclasses import dataclass
from matplotlib.figure import Figure


@dataclass
class ReportEnv:
    bot: DCSServerBot
    embed: discord.Embed = None
    figure: Figure = None
    filename: str = None
    params: dict = None

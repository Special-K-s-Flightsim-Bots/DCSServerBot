import numpy as np

from core import report
from matplotlib import cm
from plugins.competitive.commands import Competitive
from plugins.userstats.highscore import compute_font_size


class AAR(report.EmbedElement):
    async def render(self, squadron_blue: dict, squadron_red: dict):
        match_points = squadron_blue['points']
        balance = squadron_blue['total']
        starting = balance - match_points
        self.add_field(name="Blue\nStarting", value=str(starting))
        self.add_field(name="_ _\nAchieved", value=str(match_points))
        self.add_field(name="_ _\nBalance", value=str(balance))
        match_points = squadron_red['points']
        balance = squadron_red['total']
        starting = balance - match_points
        self.add_field(name="Red\nStarting", value=str(starting))
        self.add_field(name="_ _\nAchieved", value=str(match_points))
        self.add_field(name="_ _\nBalance", value=str(balance))


class TrueSkill(report.GraphElement):

    async def render(self, squadron_blue: dict, squadron_red: dict):
        labels = [
            squadron_blue['name'],
            squadron_red['name']
        ]
        values = [
            Competitive.calculate_rating(squadron_blue['trueskill']),
            Competitive.calculate_rating(squadron_red['trueskill'])
        ]

        self.axes.set_title("TrueSkill™️", color='white', fontsize=25)
        self.axes.set_xlabel("TrueSkill™️")

        num_bars = len(labels)
        if num_bars > 0:
            fontsize = compute_font_size(num_bars)
            bar_height = max(0.75, 3 / num_bars)

            color_map = cm.get_cmap('viridis', num_bars)
            colors = color_map(np.linspace(0, 1, num_bars))

            self.axes.barh(labels, values, color=colors, label="TrueSkill", height=bar_height)
            for c in self.axes.containers:
                self.axes.bar_label(c, fmt='%.1f', label_type='edge', padding=2, fontsize=fontsize)
            self.axes.margins(x=0.1)
            self.axes.tick_params(axis='y', labelsize=fontsize)
        else:
            self.axes.set_xticks([])
            self.axes.set_yticks([])
            self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)

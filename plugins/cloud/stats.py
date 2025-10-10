import matplotlib.pyplot as plt
import numpy as np
from core import report, utils, Pagination
from matplotlib.axes import Axes
from matplotlib.patches import ConnectionPatch
from pandas import DataFrame


class GuildPagination(Pagination):
    async def values(self, data: DataFrame, **kwargs) -> list[str]:
        return data['guild'].unique().tolist()


class PlaytimesPerGuild(report.GraphElement):

    async def render(self, data: DataFrame, guild: str | None = None):
        if not len(data):
            self.axes.set_visible(False)
            return

        def func(pct, allvals):
            absolute = int(round(pct / 100. * np.sum(allvals)))
            return utils.convert_time(absolute)

        labels = []
        values = []
        if guild:
            series = data[data['guild'] == guild]
        else:
            series = data
        series = series.groupby('guild').agg(total_time=('playtime', 'sum')).sort_values(by=['total_time'],
                                                                                         ascending=False).reset_index()
        for index, row in series.iterrows():
            labels.insert(0, row['guild'])
            values.insert(0, row['total_time'])
        patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                             wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
        plt.setp(pcts, color='black', fontweight='bold')
        self.axes.set_title('Time per Guild', color='white', fontsize=25)
        self.axes.axis('equal')


class PlaytimesPerPlane(report.GraphElement):

    async def render(self, data: DataFrame, guild: str | None = None):
        labels = []
        values = []
        if guild:
            series = data[data['guild'] == guild]
        else:
            series = data
        series = series.groupby('slot').agg(total_time=('playtime', 'sum')).sort_values(by=['total_time']).reset_index()
        for index, row in series.iterrows():
            labels.insert(0, row['slot'])
            values.insert(0, row['total_time'] / 3600.0)
        self.axes.bar(labels, values, width=0.5, color='mediumaquamarine')
        for label in self.axes.get_xticklabels():
            label.set_rotation(30)
            label.set_ha('right')
        self.axes.set_title('Airframe Hours per Aircraft', color='white', fontsize=25)
        self.axes.set_yticks([])
        for i in range(0, len(values)):
            self.axes.annotate('{:.1f} h'.format(values[i]), xy=(
                labels[i], values[i]), ha='center', va='bottom', weight='bold')
        if len(data) == 0:
            self.axes.set_xticks([])
            self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)


class PlaytimesPerMap(report.GraphElement):

    async def render(self, data: DataFrame, guild: str | None = None):
        if not len(data):
            self.axes.set_visible(False)
            return

        def func(pct, allvals):
            absolute = int(round(pct / 100. * np.sum(allvals)))
            return utils.convert_time(absolute)

        labels = []
        values = []
        if guild:
            series = data[data['guild'] == guild]
        else:
            series = data
        series = series.groupby('mission_theatre').agg(
            total_time=('playtime', 'sum')).sort_values(by=['total_time'], ascending=False).reset_index()
        for index, row in series.iterrows():
            labels.insert(0, row['mission_theatre'])
            values.insert(0, row['total_time'])
        patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                                             wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
        plt.setp(pcts, color='black', fontweight='bold')
        self.axes.set_title('Time per Map', color='white', fontsize=25)
        self.axes.axis('equal')


class FlightPerformance(report.GraphElement):

    async def render(self, data: DataFrame, guild: str | None = None):
        if not len(data):
            self.axes.set_visible(False)
            return

        def func(pct, allvals):
            absolute = int(round(pct / 100. * np.sum(allvals)))
            return f'{absolute}'

        labels = []
        values = []
        if guild:
            series = data[data['guild'] == guild]
        else:
            series = data
        series = series.groupby('player_ucid').agg(
            landings=('landings', 'sum'), ejections=('ejections', 'sum'), crashes=('crashes', 'sum')).reset_index()
        series['crashes'] = series['crashes'] - series['ejections']
        for name, value in series.iloc[0].items():
            if name == 'player_ucid':
                continue
            if value and value > 0:
                labels.append(name)
                values.append(value)
        if values:
            patches, texts, pcts = \
                self.axes.pie(values, labels=labels, autopct=lambda pct: func(pct, values),
                              wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
            plt.setp(pcts, color='black', fontweight='bold')
            self.axes.set_title('Flying', color='white', fontsize=25)
            self.axes.axis('equal')
        else:
            self.axes.set_visible(False)


class KDRatio(report.MultiGraphElement):

    @staticmethod
    def draw_kill_performance(ax: Axes, data: DataFrame, guild: str | None = None):
        retval = []
        if not len(data):
            ax.set_visible(False)
            return retval

        def func(pct, allvals):
            absolute = int(round(pct / 100. * np.sum(allvals)))
            return f'{absolute}'

        labels = []
        values = []
        explode = []
        if guild:
            series = data[data['guild'] == guild]
        else:
            series = data
        series = series.groupby('player_ucid').sum()
        result = DataFrame()
        result['AI Kills'] = series['kills'] - series['pvp']
        result['Player Kills'] = series['pvp']
        result['Deaths by AI'] = (series['deaths_planes'] + series['deaths_helicopters'] + series['deaths_ships'] +
                                  series['deaths_sams'] + series['deaths_ground'] - series['deaths_pvp'])
        result['Deaths by Player'] = series['deaths_pvp']
        result['Selfkill'] = (series[['deaths', 'crashes']].max(axis=1) - series['deaths_planes'] -
                              series['deaths_helicopters'] - series['deaths_ships'] - series['deaths_sams'] -
                              series['deaths_ground'])
        result['Teamkills'] = series['teamkills']
        for name, value in result.iloc[0].items():
            if value and value > 0:
                labels.append(name)
                values.append(value)
                retval.append(name)
                explode.append(0.02)
        if len(values) > 0:
            angle1 = -180 * (result['AI Kills'].values[0] + result['Player Kills'].values[0]) / np.sum(values)
            angle2 = 180 - 180 * (result['Deaths by AI'].values[0] + result['Deaths by Player'].values[0]) / np.sum(values)
            if angle1 == 0:
                angle = angle2
            elif angle2 == 180:
                angle = angle1
            else:
                angle = angle1 + (angle2 + angle1) / 2

            patches, texts, pcts = ax.pie(values, labels=labels, startangle=angle, explode=explode,
                                          autopct=lambda pct: func(pct, values),
                                          colors=['lightgreen', 'darkorange', 'lightblue'],
                                          wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'},
                                          normalize=True)
            plt.setp(pcts, color='black', fontweight='bold')
            ax.set_title('Kill/Death-Ratio', color='white', fontsize=25)
            ax.axis('equal')
        else:
            ax.set_visible(False)
        return retval

    @staticmethod
    def draw_kill_types(ax: Axes, data: DataFrame, guild: str | None = None):
        retval = False
        if not len(data):
            return retval

        labels = []
        values = []
        if guild:
            series = data[data['guild'] == guild]
        else:
            series = data
        series = series.groupby('player_ucid').agg(planes=('kills_planes', 'sum'),
                                                   helicopters=('kills_helicopters', 'sum'),
                                                   ships=('kills_ships', 'sum'),
                                                   air_defence=('kills_sams', 'sum'),
                                                   ground=('kills_ground', 'sum')).reset_index()
        for name, value in series.iloc[0].items():
            if name == 'player_ucid':
                continue
            labels.append(name.replace('_', ' ').title())
            values.append(value)
        xpos = 0
        bottom = 0
        width = 0.2
        # there is something to be drawn
        _sum = np.sum(values)
        if _sum > 0:
            for i in range(len(values)):
                height = values[i] / _sum
                ax.bar(xpos, height, width, bottom=bottom)
                ypos = bottom + ax.patches[i].get_height() / 2
                bottom += height
                if int(values[i]) > 0:
                    ax.text(xpos, ypos, f"{values[i]}", ha='center', color='black')

            ax.set_title('Killed by\nPlayer', color='white', fontsize=15)
            ax.axis('off')
            ax.set_xlim(- 2.5 * width, 2.5 * width)
            ax.legend(labels, fontsize=15, loc=3, ncol=6, mode='expand',
                      bbox_to_anchor=(-2.4, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
            # Chart was drawn, return True
            retval = True
        return retval

    @staticmethod
    def draw_death_types(ax: Axes, legend: bool, data: DataFrame, guild: str | None = None):
        retval = False
        # if no data was found, return False as no chart was drawn
        if not len(data):
            return retval

        labels = []
        values = []
        if guild:
            series = data[data['guild'] == guild]
        else:
            series = data
        series = series.groupby('player_ucid').agg(planes=('deaths_planes', 'sum'),
                                                   helicopters=('deaths_helicopters', 'sum'),
                                                   ships=('deaths_ships', 'sum'),
                                                   air_defence=('deaths_sams', 'sum'),
                                                   ground=('deaths_ground', 'sum')).reset_index()
        for name, value in series.iloc[0].items():
            if name == 'player_ucid':
                continue
            labels.append(name.replace('_', ' ').title())
            values.append(value)
        xpos = 0
        bottom = 0
        width = 0.2
        # there is something to be drawn
        _sum = np.sum(values)
        if _sum > 0:
            for i in range(len(values)):
                height = values[i] / _sum
                ax.bar(xpos, height, width, bottom=bottom)
                ypos = bottom + ax.patches[i].get_height() / 2
                bottom += height
                if int(values[i]) > 0:
                    ax.text(xpos, ypos, f"{values[i]}", ha='center', color='black')

            ax.set_title('Player\nkilled by', color='white', fontsize=15)
            ax.axis('off')
            ax.set_xlim(- 2.5 * width, 2.5 * width)
            if legend:
                ax.legend(labels, fontsize=15, loc=3, ncol=6, mode='expand',
                          bbox_to_anchor=(0.6, -0.2, 2.8, 0.4), columnspacing=1, frameon=False)
            # Chart was drawn, return True
            retval = True
        return retval

    async def render(self, data: DataFrame, guild: str | None = None):
        retval = self.draw_kill_performance(self.axes[1], data, guild)
        i = 0
        if ('AI Kills' in retval or 'Player Kills' in retval) and \
                (self.draw_kill_types(self.axes[2], data, guild) is True):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1 = self.axes[1].patches[i].theta1
            if 'AI Kills' in retval and 'Player Kills' in retval:
                i += 1
            theta2 = self.axes[1].patches[i].theta2
            center, r = self.axes[1].patches[i].center, self.axes[1].patches[i].r
            bar_height = sum([item.get_height() for item in self.axes[2].patches])

            # draw the top connecting line
            x = r * np.cos(np.pi / 180 * theta2) + center[0]
            y = r * np.sin(np.pi / 180 * theta2) + center[1]
            con = ConnectionPatch(xyA=(-0.2 / 2, bar_height), coordsA=self.axes[2].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[2].add_artist(con)

            # draw the bottom connecting line
            x = r * np.cos(np.pi / 180 * theta1) + center[0]
            y = r * np.sin(np.pi / 180 * theta1) + center[1]
            con = ConnectionPatch(xyA=(-0.2 / 2, 0), coordsA=self.axes[2].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[2].add_artist(con)
            i += 1
        else:
            self.axes[2].set_visible(False)
        if ('Deaths by AI' in retval or 'Deaths by Player' in retval) and \
                (self.draw_death_types(self.axes[0], (i == 0), data, guild) is True):
            # use ConnectionPatch to draw lines between the two plots
            # get the wedge data
            theta1 = self.axes[1].patches[i].theta1
            if 'Deaths by AI' in retval and 'Deaths by Player' in retval:
                i += 1
            theta2 = self.axes[1].patches[i].theta2
            center, r = self.axes[1].patches[i].center, self.axes[1].patches[i].r
            bar_height = sum([item.get_height() for item in self.axes[0].patches])

            # draw the top connecting line
            x = r * np.cos(np.pi / 180 * theta2) + center[0]
            y = r * np.sin(np.pi / 180 * theta2) + center[1]
            con = ConnectionPatch(xyA=(0.2 / 2, 0), coordsA=self.axes[0].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[0].add_artist(con)

            # draw the bottom connecting line
            x = r * np.cos(np.pi / 180 * theta1) + center[0]
            y = r * np.sin(np.pi / 180 * theta1) + center[1]
            con = ConnectionPatch(xyA=(0.2 / 2, bar_height), coordsA=self.axes[0].transData,
                                  xyB=(x, y), coordsB=self.axes[1].transData)
            con.set_color('lightgray')
            con.set_linewidth(2)
            con.set_linestyle('dashed')
            self.axes[0].add_artist(con)
        else:
            self.axes[0].set_visible(False)

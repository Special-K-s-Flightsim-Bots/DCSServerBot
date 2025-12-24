import discord
import io
import numpy as np
import os
import re

from core import report, utils, get_translation, GraphElement, ReportEnv, Plugin
from matplotlib import cm, pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.patches import FancyBboxPatch
from matplotlib.textpath import TextPath
from plugins.userstats.filter import StatisticsFilter
from psycopg.rows import dict_row
from typing import cast

from . import ERRORS, DISTANCE_MARKS, GRADES
from ..userstats.highscore import get_sides, compute_font_size

_ = get_translation(__name__.split('.')[1])

unicorn_image_path = 'unicorn_emoji.png'
this_dir = os.path.dirname(os.path.abspath(__file__))
unicorn_image = plt.imread(os.path.join(this_dir, 'img', unicorn_image_path))


class LSORating(report.EmbedElement):
    async def render(self, landing: dict):
        grade = GRADES[landing['grade']]
        comment = landing['comment'].replace('/', '')

        self.add_field(name=_("Date/Time"), value=f"{landing['time']:%y-%m-%d %H:%M:%S}")
        self.add_field(name=_("Plane"), value=f"{landing['unit_type']}")
        self.add_field(name=_("Carrier"), value=f"{landing['place']}")

        self.add_field(name=_("Case"), value=f"{landing['trapcase']}")
        self.add_field(name=_("Wire"), value=f"{landing['wire'] or '-'}")
        self.add_field(name=_("Points"), value=f"{landing['points']}")

        self.add_field(name=_("LSO Grade: {}").format(landing['grade'].replace('_', '\\_')), value=grade['grade'],
                       inline=False)
        self.add_field(name=_("LSO Comment"), value=comment.replace('_', '\\_'), inline=False)

        await report.Ruler(self.env).render(ruler_length=28)
        # remove unnecessary blanks
        distance_marks = list(DISTANCE_MARKS.keys())
        elements = []
        for element in [e.strip() for e in comment.split()]:
            def merge(s1: str, s2: str):
                if '(' in s1 and '(' in s2:
                    pos = s1.find(')')
                    substr2 = s2[s2.find('(') + 1:s2.find(')')]
                    s1 = s1[:pos] + substr2 + s1[pos:]
                    s2 = s2.replace('(' + substr2 + ')', '')
                if '_' in s1 and '_' in s2:
                    pos = s1.rfind('_')
                    substr2 = s2[s2.find('_') + 1:s2.rfind('_')]
                    s1 = s1[:pos] + substr2 + s1[pos:]
                    s2 = s2.replace('_' + substr2 + '_', '')
                s1 += s2
                return s1

            if len(elements) == 0:
                elements.append(element)
            else:
                if not any(distance in elements[-1] for distance in distance_marks):
                    elements[-1] = merge(elements[-1], element)
                else:
                    elements.append(element)

        for mark, text in DISTANCE_MARKS.items():
            comments = ''
            for element in elements.copy():
                if mark in element:
                    elements.remove(element)
                    if mark != 'BC':
                        element = element.replace(mark, '')

                    def deflate_comment(_element: str) -> list[str]:
                        retval = []
                        while len(_element):
                            for error in ERRORS.keys():
                                if error in _element:
                                    retval.append(ERRORS[error])
                                    _element = _element.replace(error, '')
                                    break
                            else:
                                self.log.error(f'Element {_element} not found in LSO mapping!')
                                _element = ''
                        return retval

                    little = re.findall(r"\((.*?)\)", element)
                    if len(little):
                        for x in little:
                            for y in deflate_comment(x):
                                comments += '- ' + y + _(' (a little)\n')
                            element = element.replace(f'({x})', '')
                        if not element:
                            continue
                    many = re.findall("_(.*?)_", element)
                    if len(many):
                        for x in many:
                            for y in deflate_comment(x):
                                comments += '- ' + y + _(' (a lot!)\n')
                            element = element.replace(f'_{x}_', '')
                        if not element:
                            continue
                    ignored = re.findall(r"\[(.*?)\]", element)
                    if len(ignored):
                        for x in ignored:
                            for y in deflate_comment(x):
                                comments += '- ' + y + _(' (ignored)\n')
                            element = element.replace(f'[{x}]', '')
                        if not element:
                            continue
                    for y in deflate_comment(element):
                        comments += '- ' + y + '\n'
            if len(comments) > 0:
                self.add_field(name=text, value=comments, inline=False)


class TrapSheet(report.EmbedElement):
    async def render(self, landing: dict):
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT trapsheet FROM traps WHERE id = %s", (landing['id'],))
            trapsheet = (await cursor.fetchone())[0]
            if trapsheet:
                self.env.filename = 'trapsheet.png'
                self.env.buffer = io.BytesIO(trapsheet)
                self.embed.set_image(url='attachment://trapsheet.png')


class HighscoreTraps(report.GraphElement):
    async def render(self, interaction: discord.Interaction, server_name: str, limit: int,
                     flt: StatisticsFilter, include_bolters: bool = False, include_waveoffs: bool = False,
                     bar_labels: bool | None = True):
        sql = """
            SELECT p.discord_id, COALESCE(p.name, 'Unknown') AS name, COUNT(g.*) AS value 
            FROM traps g, missions m, statistics s, players p 
            WHERE g.mission_id = m.id AND s.mission_id = m.id AND g.player_ucid = s.player_ucid 
              AND g.player_ucid = p.ucid AND g.unit_type = s.slot AND g.time BETWEEN s.hop_on AND s.hop_off
        """
        if server_name:
            sql += "AND m.server_name = %(server_name)s"
            self.env.embed.description = utils.escape_string(server_name)
            if server_name in self.bot.servers:
                sql += ' AND s.side in (' + ','.join([
                    str(x) for x in get_sides(interaction, self.bot.servers[server_name])
                ]) + ')'
        if not include_bolters:
            sql += " AND g.grade <> 'B'"
        if not include_waveoffs:
            sql += " AND g.grade NOT LIKE 'WO%%'"
        self.env.embed.title = flt.format(self.env.bot) + self.env.embed.title
        sql += ' AND ' + flt.filter(self.env.bot)
        sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                labels = []
                values = []
                await cursor.execute(sql, {"server_name": server_name})
                async for row in cursor:
                    member = self.bot.guilds[0].get_member(row['discord_id']) if row['discord_id'] != '-1' else None
                    name = member.display_name if member else row['name']
                    labels.insert(0, name)
                    values.insert(0, row['value'])
                num_bars = len(labels)
                self.axes.set_title(_("Traps"), color='white', fontsize=25)
                self.axes.set_xlabel("traps")
                if num_bars > 0:
                    fontsize = compute_font_size(num_bars)
                    bar_height = max(0.75, 3 / num_bars)

                    color_map = cm.get_cmap('viridis', num_bars)
                    colors = color_map(np.linspace(0, 1, num_bars))
                    self.axes.barh(labels, values, color=colors, label="Traps", height=bar_height)
                    if bar_labels:
                        for c in self.axes.containers:
                            self.axes.bar_label(c, fmt='%d', label_type='edge', padding=2, fontsize=fontsize)
                        self.axes.margins(x=0.1)
                    self.axes.tick_params(axis='y', labelsize=fontsize)
                else:
                    self.axes.set_xticks([])
                    self.axes.set_yticks([])
                    self.axes.text(0, 0, _('No data available.'), ha='center', va='center', rotation=45, size=15)


class GreenieBoard(GraphElement):

    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int | None = 0, col: int | None = 0,
                 colspan: int | None = 1, rowspan: int | None = 1):
        super().__init__(env, rows, cols, row, col, colspan, rowspan)
        self.plugin: Plugin = cast(Plugin, env.bot.cogs.get('GreenieBoard'))

    def add_legend(self, config: dict, start_y, card_size=0.4, font_size=14, num_landings=30, text_color='white'):
        grades = GRADES | config.get('grades', {})
        num_columns = 5 if num_landings < 20 else 3
        padding = 0.2
        x_start = 0
        y_position = start_y
        max_text_length = 0
        offset = 0
        keys = list(grades.keys())

        for i, key in enumerate(keys):
            row = i % num_columns
            col = i // num_columns

            if row == 0:
                offset += max_text_length
                max_text_length = 0

            x_pos = x_start + col * (card_size + padding) + offset
            y_pos = y_position - row * (card_size + padding)

            # Draw the unicorn image if needed
            if key == '_n':
                self.axes.plot(x_pos + card_size / 2, y_pos, 'o', color='black', markersize=10, zorder=3)
            elif key == '_OK_':
                imagebox = OffsetImage(unicorn_image, zoom=1, resample=True)
                ab = AnnotationBbox(imagebox, (x_pos + card_size / 2, y_pos), frameon=False, zorder=3)
                self.axes.add_artist(ab)
            else:
                # Draw colored rectangle for the legend
                rect = FancyBboxPatch((x_pos, y_pos - card_size / 2),
                                      card_size, card_size,
                                      boxstyle=f"round,pad=0.02,rounding_size=0.1",
                                      edgecolor='none', facecolor=grades[key]['color'],
                                      lw=0, zorder=5)
                self.axes.add_patch(rect)

            # Calculate the required space for the text
            if key == '_n':
                text = f"{grades[key]['legend']}"
            else:
                text = f"{grades[key]['legend']} ({grades[key]['rating']:.1f}) - {grades[key]['grade']}"

            text_length = len(text) * (font_size * 0.01)

            if text_length > max_text_length:
                max_text_length = text_length

            # Add text for legend dynamically adjusted
            self.axes.text(x_pos + card_size + padding, y_pos, text, va='center', ha='left', fontsize=font_size,
                           color=text_color)

    async def render(self, server_name: str, num_rows: int, num_landings: int, squadron: dict | None = None,
                     theme: str = 'dark', landings_rtl=True):

        title = self.env.embed.title
        self.env.embed.title = ""
        num_columns = num_landings
        row_height = 0.8
        column_width = 0.7
        card_size = 0.5
        text_size = 20
        font_name = None

        sql1 = """
               SELECT g.player_ucid, p.name, g.points, MAX(g.time) AS time
               FROM (SELECT player_ucid,
                            ROW_NUMBER() OVER w AS rn,
                            AVG(points) OVER w  AS points,
                            MAX(time) OVER w    AS time
                     FROM traps
               """
        sql2 = """
               SELECT TRIM(grade) as "grade", night
               FROM traps
               WHERE player_ucid = %(player_ucid)s
               """
        if server_name:
            title = utils.escape_string(server_name)
            sql1 += """
                WHERE mission_id in (
                    SELECT id FROM missions WHERE server_name = %(server_name)s
                )
            """
            sql2 += """
                AND mission_id in (
                    SELECT id FROM missions WHERE server_name = %(server_name)s
                )
            """
        if squadron:
            if self.env.embed.description:
                title += '\n'
            else:
                title = ""
            title += f"Squadron \"{utils.escape_string(squadron['name'])}\""
            if server_name:
                sql1 += " AND "
            else:
                sql1 += " WHERE "
            sql1 += """
                player_ucid IN (
                    SELECT player_ucid FROM squadron_members WHERE squadron_id = %(squadron_id)s
                )
                """
        sql1 += """
                WINDOW w AS (
                    PARTITION BY player_ucid ORDER BY ID DESC ROWS BETWEEN CURRENT ROW AND 9 FOLLOWING
                )
            ) g, players p 
            WHERE g.player_ucid = p.ucid AND g.rn = 1 
            GROUP BY 1, 2, 3 
            ORDER BY 3 DESC LIMIT %(num_rows)s
        """
        sql2 += ' ORDER BY ID DESC LIMIT %(num_landings)s'

        server = self.bot.servers.get(server_name)
        config = self.plugin.get_config(server)
        grades = GRADES | config.get('grades', {})

        if theme == 'light':
            text_color = 'black'
            bg_color = '#F5F5F5'
            odd_row_bg_color = '#D9D9D9'  # For odd rows
        else:
            text_color = 'white'
            bg_color = '#2A2A2A'
            odd_row_bg_color = '#3A3A3A'  # For odd rows

        plt.title(f'{title}', color=text_color, fontsize=30, fontname=font_name)
        plt.gca().set_facecolor(bg_color)

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(sql1, {
                    "server_name": server_name,
                    "num_rows": num_rows,
                    "squadron_id": squadron['id'] if squadron else None
                })
                rows = await cursor.fetchall()

                if not rows:
                    self.axes.axis('off')
                    xlim = self.axes.get_xlim()
                    ylim = self.axes.get_ylim()
                    self.axes.text(
                        (xlim[1] - xlim[0]) / 2 + xlim[0],  # Midpoint of x-axis
                        (ylim[1] - ylim[0]) / 2 + ylim[0],  # Midpoint of y-axis
                        'No traps captured yet.',
                        ha='center', va='center', size=35
                    )
                    return

        # Calculate dynamic figure size based on rows and columns
        max_name_width_points = 0
        font_props = FontProperties(fname=font_name, size=text_size, weight='bold')

        for item in rows:
            member = self.bot.get_member_by_ucid(item['player_ucid'])
            if member:
                item['name'] = member.display_name

            # TextPath calculates the bounding box from the font geometry itself
            tp = TextPath((0, 0), item['name'], size=text_size, prop=font_props)
            bbox = tp.get_extents()
            width_points = bbox.width

            if width_points > max_name_width_points:
                max_name_width_points = width_points

        # Check "Pilot" header too
        tp_header = TextPath((0, 0), "Pilot", size=text_size, prop=font_props)
        header_width_points = tp_header.get_extents().width

        # 72 points = 1 inch. We add a 40% buffer (1.4x) for visual breathing room.
        pilot_column_width = (max(header_width_points, max_name_width_points) / 72.0) * 1.4

        padding = 1.0  # Padding between columns
        fig_width = pilot_column_width + padding + (
                num_columns * column_width) + 2  # Additional padding on the sides

        legend_height = (5 if num_landings < 20 else 3) * (card_size + 0.2)
        fig_height = (num_rows * row_height) + 2 + legend_height  # Additional padding on the top and bottom

        if num_columns < 20:
            fig_width += 14

        self.env.figure.set_size_inches(fig_width, fig_height)

        rounding_radius = 0.1  # Radius for rounded corners

        # Plot table headers with proper padding
        self.axes.text(0, 0, "Pilot", va='center', ha='left', fontsize=text_size, color=text_color,
                       fontweight='bold', fontname=font_name)
        self.axes.text(pilot_column_width + padding, 0, "AVG", va='center', ha='center', fontsize=text_size,
                       color=text_color, fontweight='bold', fontname=font_name)

        # Add dynamic column headers directly above the card columns
        for j in range(num_columns):
            x_pos = pilot_column_width + padding + 1 + j * (card_size + 0.2) + card_size / 2
            self.axes.text(x_pos, 0, str(j + 1), va='center', ha='center', fontsize=text_size, color=text_color,
                           fontweight='bold', fontname=font_name)

        for i, row in enumerate(rows):
            y_position = -i * row_height - 1

            # Add a light gray background to odd rows
            if i % 2 == 0:
                self.axes.add_patch(plt.Rectangle((-0.5, y_position - row_height / 2), fig_width, row_height,
                                                  color=odd_row_bg_color, zorder=1))

            name = row['name']
            self.axes.text(0, y_position, name, va='center', ha='left', fontsize=text_size, color=text_color,
                           fontweight='bold', fontname=font_name)
            self.axes.text(pilot_column_width + padding, y_position, f'{row["points"]:.1f}', va='center',
                           ha='center',
                           fontsize=text_size, color=text_color, fontname=font_name)

            async with self.apool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(sql2, {
                        "player_ucid": row['player_ucid'],
                        "server_name": server_name,
                        "num_landings": num_landings
                    })

                    landings = await cursor.fetchall()
                    if not landings_rtl:
                        landings.reverse()

            for j in range(num_columns):
                x_pos = pilot_column_width + padding + 1 + j * (card_size + 0.2)
                if j < len(landings):
                    grade = landings[j]['grade']

                    # fixing grades...
                    if grade in ['WOP', 'OWO', 'TWO', 'TLU']:
                        grade = 'NC'
                    elif grade == 'WOFD':
                        grade = 'WO'

                    if grade == '_OK_':
                        imagebox = OffsetImage(unicorn_image, zoom=1, resample=True)
                        ab = AnnotationBbox(imagebox, (x_pos + card_size / 2, y_position), frameon=False,
                                            zorder=3)
                        self.axes.add_artist(ab)
                    else:
                        rect = FancyBboxPatch((x_pos, y_position - card_size / 2),
                                              card_size, card_size,
                                              boxstyle="round,pad=0.02,rounding_size=0.1",
                                              edgecolor='none', facecolor=grades[grade]['color'],
                                              lw=0, zorder=2)
                        self.axes.add_patch(rect)
                    # mark night passes
                    if landings[j]['night']:
                        self.axes.plot(x_pos + card_size / 2, y_position, 'o', color='black', markersize=10,
                                       zorder=3)

                else:
                    # Draw true rounded brackets using arcs and lines
                    y_top = y_position + card_size / 2
                    y_bottom = y_position - card_size / 2

                    # Left rounded bracket
                    self.axes.plot([x_pos + rounding_radius, x_pos], [y_top, y_top - rounding_radius],
                                   color='grey', lw=1.5)
                    self.axes.plot([x_pos, x_pos], [y_top - rounding_radius, y_bottom + rounding_radius],
                                   color='grey', lw=1.5)
                    self.axes.plot([x_pos, x_pos + rounding_radius], [y_bottom + rounding_radius, y_bottom],
                                   color='grey', lw=1.5)

                    # Right rounded bracket
                    x_right = x_pos + card_size
                    self.axes.plot([x_right - rounding_radius, x_right], [y_top, y_top - rounding_radius],
                                   color='grey', lw=1.5)
                    self.axes.plot([x_right, x_right], [y_top - rounding_radius, y_bottom + rounding_radius],
                                   color='grey', lw=1.5)
                    self.axes.plot([x_right, x_right - rounding_radius], [y_bottom + rounding_radius, y_bottom],
                                   color='grey', lw=1.5)

                legend_start_y = -row_height * num_rows - 1.5
                self.add_legend(config=config, start_y=legend_start_y, num_landings=num_landings, text_color=text_color)
                self.axes.set_xlim(-0.5, fig_width - 0.5)
                self.axes.set_ylim(-fig_height + 1, 0.5)
                self.axes.axis('off')
                self.env.figure.patch.set_facecolor(bg_color)

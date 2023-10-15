from __future__ import annotations
import discord
import inspect
import numpy as np
import os
import sys
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from core import utils
from datetime import timedelta
from discord import ButtonStyle, Interaction
from matplotlib import pyplot as plt
from psycopg.rows import dict_row
from typing import Optional, Any, TYPE_CHECKING, Union

from .env import ReportEnv
from .errors import UnknownGraphElement, ClassNotFound, TooManyElements, UnknownValue, NothingToPlot
from .__utils import parse_params


if TYPE_CHECKING:
    from core import DCSServerBot

__all__ = [
    "ReportElement",
    "EmbedElement",
    "Image",
    "Ruler",
    "Field",
    "Table",
    "Button",
    "GraphElement",
    "MultiGraphElement",
    "Graph",
    "SQLField",
    "SQLTable",
    "BarChart",
    "SQLBarChart",
    "PieChart",
    "SQLPieChart"
]


class ReportElement(ABC):
    def __init__(self, env: ReportEnv):
        self.env = env
        self.bot: DCSServerBot = env.bot
        self.node = self.bot.node
        self.log = env.bot.log
        self.pool = env.bot.pool

    @abstractmethod
    def render(self, **kwargs):
        pass


class EmbedElement(ReportElement):
    def __init__(self, env: ReportEnv):
        super().__init__(env)
        self.embed = env.embed

    def add_field(self, *, name, value, inline=True):
        return self.embed.add_field(name=name[:256] or '_ _',
                                    value=(value[:1024] if isinstance(value, str) else value) or '_ _',
                                    inline=inline)

    def set_image(self, *, url):
        return self.embed.set_image(url=url)

    @abstractmethod
    def render(self, **kwargs):
        pass


class Image(EmbedElement):
    def render(self, url: str):
        self.set_image(url=url)


class Ruler(EmbedElement):
    def render(self, header: Optional[str] = '', ruler_length: Optional[int] = 34, *, text: Optional[str] = None):
        if header:
            header = ' ' + header + ' '
        filler = int((ruler_length - len(header) / 2.5) / 2)
        if filler <= 0:
            filler = 1
        self.add_field(name='▬' * filler + header + '▬' * filler, value=text or '_ _', inline=False)


class Field(EmbedElement):
    def render(self, name: str, value: Any, inline: Optional[bool] = True):
        self.add_field(name=utils.format_string(name, '_ _', **self.env.params),
                       value=utils.format_string(value, '_ _', **self.env.params), inline=inline)


class Table(EmbedElement):
    def render(self, values: Union[dict, list[dict]], obj: Optional[str] = None, inline: Optional[bool] = True):
        if obj:
            table = self.env.params[obj]
            _values: dict = values.copy()
            values = list[dict]()
            if isinstance(table, list):
                for row in table:
                    values.append({_values[k]: v for k, v in row.items() if k in _values.keys()})
            elif isinstance(table, dict):
                values.append({_values[k]: v for k, v in table.items() if k in _values.keys()})
        header = None
        cols = ['', '', '']
        elements = 0
        for row in values:
            elements = len(row)
            if elements > 3:
                raise TooManyElements(elements)
            if not header:
                header = list(row.keys())
            for i in range(0, elements):
                cols[i] += utils.format_string(str(row[header[i]]), '_ _', **self.env.params) + '\n'
        for i in range(0, elements):
            self.add_field(name=header[i], value=cols[i], inline=inline)
        if inline:
            for i in range(elements, 3):
                self.add_field(name='_ _', value='_ _')


class Button(ReportElement):
    def render(self, style: str, label: str, custom_id: Optional[str] = None, url: Optional[str] = None,
               disabled: Optional[bool] = False, interaction: Optional[Interaction] = None):
        b = discord.ui.Button(style=ButtonStyle(style), label=label, url=url, disabled=disabled)
        if interaction:
            b.callback(interaction=interaction)
        if not self.env.view:
            self.env.view = discord.ui.View()
        self.env.view.add_item(b)


class GraphElement(ReportElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int, col: int,
                 colspan: Optional[int] = 1, rowspan: Optional[int] = 1):
        super().__init__(env)
        self.axes = plt.subplot2grid((rows, cols), (row, col), colspan=colspan, rowspan=rowspan, fig=self.env.figure)

    @abstractmethod
    def render(self, **kwargs):
        pass


class MultiGraphElement(ReportElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, params: list[dict]):
        super().__init__(env)
        self.axes = []
        for i in range(0, len(params)):
            colspan = params[i]['colspan'] if 'colspan' in params[i] else 1
            rowspan = params[i]['rowspan'] if 'rowspan' in params[i] else 1
            sharex = params[i]['sharex'] if 'sharex' in params[i] else False
            self.axes.append(plt.subplot2grid((rows, cols), (params[i]['row'], params[i]['col']), colspan=colspan,
                                              rowspan=rowspan, fig=self.env.figure,
                                              sharex=self.axes[-1] if sharex else None))

    @abstractmethod
    def render(self, **kwargs):
        pass


class Graph(ReportElement):
    def __init__(self, env: ReportEnv):
        super().__init__(env)
        plt.switch_backend('agg')

    def render(self, width: int, height: int, cols: int, rows: int, elements: list[dict],
               facecolor: Optional[str] = None):
        plt.style.use('dark_background')
        plt.rcParams['axes.facecolor'] = '2C2F33'
        if 'cjk_font' in self.bot.locals.get('reports', {}):
            plt.rcParams['font.family'] = [f"Noto Sans {self.bot.locals['reports']['cjk_font']}", 'sans-serif']
        self.env.figure = plt.figure(figsize=(width, height))
        if facecolor:
            self.env.figure.set_facecolor(facecolor)
        futures = []
        with ThreadPoolExecutor(
                max_workers=int(self.env.bot.locals.get('reports', {}).get('num_workers', 4))) as executor:
            for element in elements:
                if 'params' in element:
                    element_args = parse_params(self.env.params, element['params'])
                else:
                    element_args = self.env.params.copy()
                element_class = utils.str_to_class(element['class']) if 'class' in element else None
                if not element_class and 'type' in element:
                    element_class = getattr(sys.modules[__name__], element['type'])
                if element_class:
                    # remove parameters, that are not in the class __init__ signature
                    signature = inspect.signature(element_class.__init__).parameters.keys()
                    class_args = {name: value for name, value in element_args.items() if name in signature}
                    # instantiate the class
                    element_class = element_class(self.env, rows, cols, **class_args)
                    if isinstance(element_class, GraphElement) or isinstance(element_class, MultiGraphElement):
                        # remove parameters, that are not in the render methods signature
                        signature = inspect.signature(element_class.render).parameters.keys()
                        render_args = {name: value for name, value in element_args.items() if name in signature}
                        futures.append(executor.submit(element_class.render, **render_args))
                    else:
                        raise UnknownGraphElement(element['class'])
                else:
                    raise ClassNotFound(element['class'])
        # check for any exceptions and raise them
        for future in futures:
            if future.exception():
                if isinstance(future.exception(), NothingToPlot):
                    return
                raise future.exception()
        # only render the graph, if we don't have a rendered graph already attached as a file (image)
        if not self.env.filename:
            plt.subplots_adjust(hspace=0.5, wspace=0.5)
            self.env.filename = f'{uuid.uuid4()}.png'
            self.env.figure.savefig(self.env.filename, bbox_inches='tight', facecolor='#2C2F33')
            plt.close(self.env.figure)
        self.env.embed.set_image(url='attachment://' + os.path.basename(self.env.filename))
        footer = self.env.embed.footer.text
        if footer is None:
            footer = 'Click on the image to zoom in.'
        else:
            footer += '\nClick on the image to zoom in.'
        self.env.embed.set_footer(text=footer)


class SQLField(EmbedElement):
    def render(self, sql: str, inline: Optional[bool] = True):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                if cursor.rowcount > 0:
                    row = cursor.fetchone()
                    name = list(row.keys())[0]
                    value = row[name]
                    self.add_field(name=name, value=value, inline=inline)


class SQLTable(EmbedElement):
    def render(self, sql: str, inline: Optional[bool] = True):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                header = None
                cols = []
                elements = 0
                for row in cursor.fetchall():
                    elements = len(row)
                    if not header:
                        header = list(row.keys())
                    values = list(row.values())
                    for i in range(0, elements):
                        if len(cols) <= i:
                            cols.append(str(values[i]) + '\n')
                        else:
                            cols[i] += str(values[i]) + '\n'
                for i in range(0, elements):
                    self.add_field(name=header[i], value=cols[i], inline=inline)
                if elements % 3 and inline:
                    for i in range(0, 3 - elements % 3):
                        self.add_field(name='_ _', value='_ _')


class BarChart(GraphElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int, col: int, colspan: Optional[int] = 1,
                 rowspan: Optional[int] = 1, title: Optional[str] = '', color: Optional[str] = None,
                 rotate_labels: Optional[int] = 0, bar_labels: Optional[bool] = False, is_time: Optional[bool] = False,
                 orientation: Optional[str] = 'vertical', width: Optional[float] = 0.5,
                 show_no_data: Optional[bool] = True):
        super().__init__(env, rows, cols, row, col, colspan, rowspan)
        self.title = title
        self.color = color
        self.rotate_labels = rotate_labels
        self.bar_labels = bar_labels
        self.is_time = is_time
        self.orientation = orientation
        self.width = width
        self.show_no_data = show_no_data

    def render(self, values: dict[str, float]):
        if len(values) or self.show_no_data:
            labels = list(values.keys())
            values = list(values.values())
            if self.orientation == 'vertical':
                self.axes.bar(labels, values, width=self.width, color=self.color)
            elif self.orientation == 'horizontal':
                self.axes.barh(labels, values, height=self.width, color=self.color)
            else:
                raise UnknownValue('orientation', self.orientation)
            self.axes.set_title(self.title, color='white', fontsize=25)
            if self.rotate_labels > 0:
                for label in self.axes.get_xticklabels():
                    label.set_rotation(self.rotate_labels)
                    label.set_ha('right')
            if self.bar_labels:
                for c in self.axes.containers:
                    self.axes.bar_label(c, fmt='%.1f h' if self.is_time else '%.1f', label_type='edge')
            if len(values) == 0:
                self.axes.set_xticks([])
                self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        else:
            self.axes.set_visible(False)


class SQLBarChart(BarChart):
    def render(self, sql: str):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                if cursor.rowcount == 1:
                    super().render(cursor.fetchone())
                elif cursor.rowcount > 1:
                    values = {}
                    for row in cursor.fetchall():
                        d = list(row.values())
                        values[d[0]] = d[1]
                    super().render(values)
                else:
                    super().render({})


class PieChart(GraphElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int, col: int, colspan: Optional[int] = 1,
                 rowspan: Optional[int] = 1, title: Optional[str] = '', colors: Optional[list[str]] = None,
                 is_time: Optional[bool] = False, show_no_data: Optional[bool] = True):
        super().__init__(env, rows, cols, row, col, colspan, rowspan)
        self.title = title
        self.colors = colors
        self.is_time = is_time
        self.show_no_data = show_no_data

    def func(self, pct, allvals):
        absolute = int(round(pct / 100. * np.sum(allvals)))
        if self.is_time:
            return '{:.1f}%\n({:s}h)'.format(pct, str(timedelta(seconds=absolute)))
        else:
            return '{:.1f}%\n({:d})'.format(pct, absolute)

    def render(self, values: dict[str, Any]):
        values = {k: v for k, v in values.copy().items() if v}
        if len(values) or self.show_no_data:
            labels = values.keys()
            values = list(values.values())
            patches, texts, pcts = self.axes.pie(values, labels=labels, autopct=lambda pct: self.func(pct, values),
                                                 wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True)
            plt.setp(pcts, color='black', fontweight='bold')
            self.axes.set_title(self.title, color='white', fontsize=25)
            self.axes.axis('equal')
            if len(values) == 0:
                self.axes.set_xticks([])
                self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        else:
            self.axes.set_visible(False)


class SQLPieChart(PieChart):
    def render(self, sql: str):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                if cursor.rowcount == 1:
                    super().render(cursor.fetchone())
                elif cursor.rowcount > 1:
                    values = {}
                    for row in cursor.fetchall():
                        d = list(row.values())
                        values[d[0]] = d[1]
                    super().render(values)
                else:
                    super().render({})

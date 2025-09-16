from __future__ import annotations

import asyncio
import discord
import inspect
import numpy as np
import os
import re
import sys
import traceback
import uuid

from abc import ABC, abstractmethod
from core import utils
from datetime import timedelta, datetime
from discord import ButtonStyle, Interaction
from io import BytesIO
from matplotlib import pyplot as plt
from psycopg.rows import dict_row
from typing import Optional, Any, TYPE_CHECKING, Union

from .env import ReportEnv
from .errors import UnknownGraphElement, ClassNotFound, TooManyElements, UnknownValue, NothingToPlot
from .__utils import parse_params


if TYPE_CHECKING:
    from services.bot import DCSServerBot

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

_languages = None


def get_supported_fonts() -> set[str]:
    global _languages

    if _languages is None:
        _languages = set()
        if os.path.exists('fonts'):
            for filename in os.listdir('fonts'):
                if filename.startswith("NotoSans"):
                    match = re.search(r"NotoSans(..)-", filename)
                    if match:
                        lang = match.group(1)
                        _languages.add(lang)
    return _languages


class ReportElement(ABC):
    def __init__(self, env: ReportEnv):
        self.env = env
        self.bot: DCSServerBot = env.bot
        self.node = self.bot.node
        self.log = env.bot.log
        self.pool = env.bot.pool
        self.apool = env.bot.apool

    @abstractmethod
    async def render(self, **kwargs) -> None:
        ...


class EmbedElement(ReportElement):
    def __init__(self, env: ReportEnv):
        super().__init__(env)
        self.embed = env.embed

    def add_field(self, *, name: str, value: str, inline=True) -> discord.Embed:
        if len(self.embed.fields) >= 25:
            self.log.warning(f"Can't add field '{name}': too many fields in embed.")
            return self.embed
        if name is None or name == '':
            name = '_ _'
        else:
            name = str(name)
        if len(name) > 256:
            name = name[:252] + ' ...'
        if value is None or value == '':
            value = '_ _'
        else:
            value = str(value)
        if len(value) > 1024:
            value = value[:1020] + ' ...'
        return self.embed.add_field(name=name or '_ _', value=value or '_ _', inline=inline)

    def add_datetime_field(self, name: str, time_obj: datetime):
        if time_obj != datetime(1970, 1, 1):
            if time_obj.year == 9999:
                value = 'never'
            else:
                value = f'<t:{int(time_obj.timestamp())}:R>\n({time_obj.strftime("%y-%m-%d %H:%Mz")})'
            self.add_field(name=f'{name}:', value=value)

    def set_image(self, *, url):
        return self.embed.set_image(url=url)

    @abstractmethod
    async def render(self, **kwargs):
        ...


class Image(EmbedElement):
    async def render(self, url: str):
        self.set_image(url=url)


class Ruler(EmbedElement):
    async def render(self, header: Optional[str] = '', ruler_length: Optional[int] = 34, *, text: Optional[str] = None):
        self.add_field(name=utils.print_ruler(header=header, ruler_length=ruler_length),
                       value=text or '_ _', inline=False)


class Field(EmbedElement):
    async def render(self, name: str, value: Any, inline: Optional[bool] = True, default: Optional[str] = '_ _'):
        self.add_field(name=utils.format_string(name, '_ _', **self.env.params),
                       value=utils.format_string(value, default, **self.env.params), inline=inline)


class Table(EmbedElement):
    async def render(self, values: Union[dict, list[dict]], obj: Optional[str] = None, inline: Optional[bool] = True,
                     ansi_colors: Optional[bool] = False):
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
        cols = ['', '', ''] if not ansi_colors else ['```ansi\n', '```ansi\n', '```ansi\n']
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
            self.add_field(name=header[i], value=cols[i] + ('```' if ansi_colors else ''), inline=inline)
        if inline:
            for i in range(elements, 3):
                self.add_field(name='_ _', value='_ _')


class Button(ReportElement):
    async def render(self, style: str, label: str, custom_id: Optional[str] = None, url: Optional[str] = None,
                     disabled: Optional[bool] = False, interaction: Optional[Interaction] = None):
        b = discord.ui.Button(style=ButtonStyle(style), label=label, url=url, disabled=disabled)
        if interaction:
            await b.callback(interaction=interaction)
        if not self.env.view:
            self.env.view = discord.ui.View()
        self.env.view.add_item(b)


class GraphElement(ReportElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, row: Optional[int] = 0, col: Optional[int] = 0,
                 colspan: Optional[int] = 1, rowspan: Optional[int] = 1, polar: Optional[bool] = False):
        super().__init__(env)
        self.axes = plt.subplot2grid((rows, cols), (row, col), colspan=colspan, rowspan=rowspan,
                                     fig=self.env.figure, polar=polar)

    @abstractmethod
    async def render(self, **kwargs):
        ...


class MultiGraphElement(ReportElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, params: list[dict],
                 wspace: float = 0.5, hspace: float = 0.5):
        super().__init__(env)
        self.axes = []
        self.wspace = wspace
        self.hspace = hspace
        for i in range(0, len(params)):
            colspan = params[i]['colspan'] if 'colspan' in params[i] else 1
            rowspan = params[i]['rowspan'] if 'rowspan' in params[i] else 1
            sharex = params[i]['sharex'] if 'sharex' in params[i] else False
            self.axes.append(plt.subplot2grid((rows, cols), (params[i]['row'], params[i]['col']),
                                              colspan=colspan, rowspan=rowspan, fig=self.env.figure,
                                              sharex=self.axes[-1] if sharex else None,
                                              polar=params[i].get('polar', False)))

        plt.subplots_adjust(wspace=self.wspace, hspace=self.hspace)

    @abstractmethod
    async def render(self, **kwargs):
        ...


class Graph(ReportElement):
    def __init__(self, env: ReportEnv, width: int, height: int, cols: int, rows: int, elements: list[dict],
                     wspace: float = 0.5, hspace: float = 0.5, dpi = 100, facecolor: Optional[str] = '#2C2F33'):
        super().__init__(env)
        plt.switch_backend('agg')
        self.width = width
        self.height = height
        self.cols = cols
        self.rows = rows
        self.elements = elements
        self.wspace = wspace
        self.hspace = hspace
        self.dpi = dpi
        self.facecolor = facecolor
        self.plot_lock = asyncio.Lock()

    def _plot(self):
        plt.subplots_adjust(wspace=self.wspace, hspace=self.hspace)
        self.env.filename = f'{uuid.uuid4()}.png'

        # Save with adjusted dimensions while maintaining aspect ratio
        self.env.buffer = BytesIO()
        self.env.figure.savefig(
            self.env.buffer,
            format='png',
            bbox_inches='tight',
            dpi=self.dpi
        )
        self.env.buffer.seek(0)

    async def _async_plot(self):
        async with self.plot_lock:
            self._plot()

    async def render(self, **kwargs):
        plt.style.use('dark_background')
        plt.rcParams['axes.facecolor'] = self.facecolor
        plt.rcParams['figure.facecolor'] = self.facecolor
        plt.rcParams['savefig.facecolor'] = self.facecolor
        fonts = get_supported_fonts()
        if fonts:
            plt.rcParams['font.family'] = [f"Noto Sans {x}" for x in fonts] + ['sans-serif']
        self.env.figure = plt.figure(figsize=(self.width, self.height), dpi=self.dpi)
        try:
            if self.facecolor:
                self.env.figure.set_facecolor(self.facecolor)
                self.env.figure.patch.set_facecolor(self.facecolor)
            tasks = []
            for element in self.elements:
                if 'params' in element:
                    element_args = parse_params(self.env.params, element['params'])
                else:
                    element_args = self.env.params.copy()
                element_class = utils.str_to_class(element['class']) if 'class' in element else None
                if not element_class and 'type' in element:
                    element_class = getattr(sys.modules[__name__], element['type'])
                if element_class:
                    # remove the parameters that are not in the class __init__ signature
                    signature = inspect.signature(element_class.__init__).parameters.keys()
                    class_args = {name: value for name, value in element_args.items() if name in signature}
                    # instantiate the class
                    element_class = element_class(self.env, self.rows, self.cols, **class_args)
                    if isinstance(element_class, (GraphElement, MultiGraphElement)):
                        # remove the parameters that are not in the render methods signature
                        signature = inspect.signature(element_class.render).parameters.keys()
                        render_args = {name: value for name, value in element_args.items() if name in signature}
                        tasks.append(asyncio.create_task(element_class.render(**render_args)))
                    else:
                        raise UnknownGraphElement(element['class'])
                else:
                    raise ClassNotFound(element['class'])
            # check for any exceptions and print them
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    if not isinstance(result, NothingToPlot):
                        self.log.error("Error in Graph:\n{stacktrace}".format(
                            stacktrace=''.join(traceback.format_exception(result)))
                        )

            # only render the graph if we don't have a rendered graph already attached as a file (image)
            if not self.env.filename:
                await self._async_plot()
            self.env.embed.set_image(url='attachment://' + os.path.basename(self.env.filename))
            footer = self.env.embed.footer.text or ''
            if footer is None:
                footer = 'Click on the image to zoom in.'
            else:
                footer += '\nClick on the image to zoom in.'
            self.env.embed.set_footer(text=footer)
        finally:
            if self.env.figure:
                plt.close(self.env.figure)
                self.env.figure = None


def _display_no_data(element: EmbedElement, no_data: Union[str, dict], inline: bool):
    if isinstance(no_data, str):
        element.add_field(name='_ _', value=no_data, inline=inline)
    else:
        for name, value in no_data.items():
            element.add_field(name=name, value=value, inline=inline)


class SQLField(EmbedElement):
    async def render(self, sql: str, inline: Optional[bool] = True, no_data: Optional[Union[str, dict]] = None,
                     on_error: Optional[dict] = None):
        try:
            async with self.apool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                    if cursor.rowcount > 0:
                        row = await cursor.fetchone()
                        name = list(row.keys())[0]
                        value = row[name]
                        if isinstance(value, datetime):
                            value = value.strftime('%Y-%m-%d %H:%M')
                        self.add_field(name=name, value=value, inline=inline)
                    else:
                        if no_data:
                            _display_no_data(self, no_data, inline)
        except Exception as ex:
            if on_error:
                self.add_field(name=list(on_error.keys())[0],
                               value=utils.format_string(str(list(on_error.values())[0]), ex=ex), inline=inline)
            else:
                raise


class SQLTable(EmbedElement):
    async def render(self, sql: str, inline: Optional[bool] = True, no_data: Optional[Union[str, dict]] = None,
                     ansi_colors: Optional[bool] = False, on_error: Optional[dict] = None):
        try:
            async with self.apool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                    if cursor.rowcount == 0:
                        if no_data:
                            _display_no_data(self, no_data, False)
                        return
                    header = None
                    cols = []
                    elements = 0
                    async for row in cursor:
                        elements = len(row)
                        if not header:
                            header = list(row.keys())
                        values = list(row.values())
                        for i in range(0, elements):
                            if isinstance(values[i], datetime):
                                value = values[i].strftime('%Y-%m-%d %H:%M')
                            else:
                                value = str(values[i])
                            if len(cols) <= i:
                                cols.append(('```ansi\n' if ansi_colors else '') + value + '\n')
                            else:
                                cols[i] += value + '\n'
                    for i in range(0, elements):
                        self.add_field(name=header[i], value=cols[i] + ('```' if ansi_colors else ''), inline=inline)
                    if elements % 3 and inline:
                        for i in range(0, 3 - elements % 3):
                            self.add_field(name='_ _', value='_ _')
        except Exception as ex:
            if on_error:
                for key, value in on_error.items():
                    self.add_field(name=key, value=utils.format_string(value, ex=ex), inline=inline)
            else:
                raise


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

    async def render(self, values: dict[str, float]):
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
                    label.set_horizontalalignment('right')
            if self.bar_labels:
                for c in self.axes.containers:
                    self.axes.bar_label(c, fmt='%.1f h' if self.is_time else '%.1f', label_type='edge', padding=2)
                # increase the padding by 10% to allow the texts
                self.axes.margins(x=0.1)
            if len(values) == 0:
                self.axes.set_xticks([])
                self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        else:
            self.axes.set_visible(False)


class SQLBarChart(BarChart):
    async def render(self, sql: str):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                if cursor.rowcount == 1:
                    await super().render(await cursor.fetchone())
                elif cursor.rowcount > 1:
                    values = {}
                    async for row in cursor:
                        d = list(row.values())
                        values[d[0]] = d[1]
                    await super().render(values)
                else:
                    await super().render({})


class PieChart(GraphElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int, col: int, colspan: Optional[int] = 1,
                 rowspan: Optional[int] = 1, title: Optional[str] = '', colors: Optional[list[str]] = None,
                 is_time: Optional[bool] = False, show_no_data: Optional[bool] = True,
                 textcolor: Optional[str] = 'black'):
        super().__init__(env, rows, cols, row, col, colspan, rowspan)
        self.title = title
        self.colors = colors
        self.textcolor = textcolor
        self.is_time = is_time
        self.show_no_data = show_no_data

    def func(self, pct, allvals):
        absolute = int(round(pct / 100. * np.sum(allvals)))
        if self.is_time:
            return '{:.1f}%\n({:s}h)'.format(pct, str(timedelta(seconds=absolute)))
        else:
            return '{:.1f}%\n({:d})'.format(pct, absolute)

    async def render(self, values: dict[str, Any]):
        values = {k: v for k, v in values.copy().items() if v}
        if len(values) or self.show_no_data:
            labels = values.keys()
            values = list(values.values())
            patches, texts, pcts = self.axes.pie(
                values, labels=labels, autopct=lambda pct: self.func(pct, values), colors=self.colors,
                wedgeprops={'linewidth': 3.0, 'edgecolor': 'black'}, normalize=True
            )
            plt.setp(pcts, color=self.textcolor, fontweight='bold')
            self.axes.set_title(self.title, color='white', fontsize=25)
            self.axes.axis('equal')
            if len(values) == 0:
                self.axes.set_xticks([])
                self.axes.text(0, 0, 'No data available.', ha='center', va='center', rotation=45, size=15)
        else:
            self.axes.set_visible(False)


class SQLPieChart(PieChart):
    async def render(self, sql: str):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                if cursor.rowcount == 1:
                    await super().render(await cursor.fetchone())
                elif cursor.rowcount > 1:
                    values = {}
                    async for row in cursor:
                        d = list(row.values())
                        values[d[0]] = d[1]
                    await super().render(values)
                else:
                    await super().render({})

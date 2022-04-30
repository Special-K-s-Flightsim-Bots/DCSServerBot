import concurrent
import discord
import inspect
import numpy as np
import psycopg2
import sys
import uuid
from abc import ABC, abstractmethod
from contextlib import closing
from core import utils
from core.report.env import ReportEnv
from core.report.errors import UnknownGraphElement, ClassNotFound, TooManyElements, UnknownValue
from core.report.utils import parse_params
from datetime import timedelta
from matplotlib import pyplot as plt
from typing import Optional, List, Any


class ReportElement(ABC):
    def __init__(self, env: ReportEnv):
        self.env = env
        self.bot = env.bot
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
        return self.embed.add_field(name=name[:256],
                                    value=value[:1024] if isinstance(value, str) else value,
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
    def render(self, ruler_length: Optional[int] = 25):
        self.add_field(name='▬' * ruler_length, value='_ _', inline=False)


class Field(EmbedElement):
    def render(self, name: str, value: Any, inline: Optional[bool] = True):
        self.add_field(name=name, value=utils.format_string(value, '_ _', **self.env.params), inline=inline)


class Table(EmbedElement):
    def render(self, values: dict):
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
                cols[i] += utils.format_string(row[header[i]], '_ _', **self.env.params)
        for i in range(0, elements):
            self.add_field(name=header[i], value=cols[i])
        for i in range(elements, 3):
            self.add_field(name='_ _', value='_ _')


class GraphElement(ReportElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int, col: int,
                 colspan: Optional[int] = 1, rowspan: Optional[int] = 1):
        super().__init__(env)
        self.axes = plt.subplot2grid((rows, cols), (row, col), colspan=colspan, rowspan=rowspan, fig=self.env.figure)

    @abstractmethod
    def render(self, **kwargs):
        pass


class MultiGraphElement(ReportElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, params: List[dict]):
        super().__init__(env)
        self.axes = []
        for i in range(0, len(params)):
            colspan = params[i]['colspan'] if 'colspan' in params[i] else 1
            rowspan = params[i]['rowspan'] if 'rowspan' in params[i] else 1
            self.axes.append(plt.subplot2grid((rows, cols), (params[i]['row'], params[i]['col']), colspan=colspan,
                                              rowspan=rowspan, fig=self.env.figure))

    @abstractmethod
    def render(self, **kwargs):
        pass


class Graph(ReportElement):
    def __init__(self, env: ReportEnv):
        super().__init__(env)
        plt.switch_backend('agg')

    def render(self, width: int, height: int, cols: int, rows: int, elements: List[dict]):
        plt.style.use('dark_background')
        plt.rcParams['axes.facecolor'] = '2C2F33'
        self.env.figure = plt.figure(figsize=(width, height))
        futures = []
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=int(self.env.bot.config['REPORTS']['NUM_WORKERS'])) as executor:
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
                raise future.exception()
        plt.subplots_adjust(hspace=0.5, wspace=0.5)
        self.env.filename = f'{uuid.uuid4()}.png'
        self.env.figure.savefig(self.env.filename, bbox_inches='tight', facecolor='#2C2F33')
        plt.close(self.env.figure)
        self.env.embed.set_image(url='attachment://' + self.env.filename)
        footer = self.env.embed.footer.text
        if footer == discord.Embed.Empty:
            footer = 'Click on the image to zoom in.'
        else:
            footer += '\nClick on the image to zoom in.'
        self.env.embed.set_footer(text=footer)


class SQLField(EmbedElement):
    def render(self, sql: str, inline: Optional[bool] = True):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                if cursor.rowcount > 0:
                    row = cursor.fetchone()
                    name = list(row.keys())[0]
                    value = row[0]
                    self.add_field(name=name, value=value, inline=inline)
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class SQLTable(EmbedElement):
    def render(self, sql: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(utils.format_string(sql, **self.env.params), self.env.params)
                header = None
                cols = ['', '', '']
                elements = 0
                for row in cursor.fetchall():
                    elements = len(row)
                    if elements > 3:
                        raise TooManyElements(elements)
                    if not header:
                        header = list(row.keys())
                    for i in range(0, elements):
                        cols[i] += str(row[i])
                for i in range(0, elements):
                    self.add_field(name=header[i], value=cols[i])
                for i in range(elements, 3):
                    self.add_field(name='_ _', value='_ _')
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
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
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


class PieChart(GraphElement):
    def __init__(self, env: ReportEnv, rows: int, cols: int, row: int, col: int, colspan: Optional[int] = 1,
                 rowspan: Optional[int] = 1, title: Optional[str] = '', colors: Optional[List[str]] = None,
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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
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
        except psycopg2.DatabaseError as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

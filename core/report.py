import asyncio
import concurrent
import discord
import inspect
import json
import matplotlib.pyplot as plt
import os
import psycopg2
import string
import sys
import uuid
from abc import abstractmethod, ABC
from contextlib import closing, suppress
from core import utils, DCSServerBot
from dataclasses import dataclass
from discord.ext.commands import Context
from matplotlib.figure import Figure
from os import path
from typing import Any, List, Optional, Tuple


class UnknownReportElement(Exception):
    def __init__(self, classname: str):
        super().__init__(f'The class {classname} is not a ReportElement.')


class UnknownGraphElement(Exception):
    def __init__(self, classname: str):
        super().__init__(f'The class {classname} is not a GraphElement or MultiGraphElement.')


class ClassNotFound(Exception):
    def __init__(self, classname: str):
        super().__init__(f'The class {classname} could not be found.')


class ValueNotInRange(Exception):
    def __init__(self, name, value: Any, range_: List[Any]):
        super().__init__('Value "{}" of parameter {} is not in the allowed range of [{}]'.format(value, name, ', '.join(
            f'"{x}"' for x in range_)))


@dataclass
class ReportEnv:
    bot: DCSServerBot
    embed: discord.Embed = None
    figure: Figure = None
    filename: str = None
    params: dict = None


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
        return self.embed.add_field(name=name, value=value, inline=inline)

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
            self.axes.append(plt.subplot2grid((rows, cols), (params[i]['row'], params[i]['col']), colspan=colspan, rowspan=rowspan, fig=self.env.figure))

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
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=int(self.env.bot.config['REPORTS']['NUM_WORKERS'])) as executor:
            for element in elements:
                if 'params' in element:
                    element_args = Report.parse_params(self.env.params, element['params'])
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
                        executor.submit(element_class.render, **render_args)
                    else:
                        raise UnknownGraphElement(element['class'])
                else:
                    raise ClassNotFound(element['class'])
        plt.subplots_adjust(hspace=0.5, wspace=0.5)
        self.env.filename = f'{uuid.uuid4()}.png'
        self.env.figure.savefig(self.env.filename, bbox_inches='tight', facecolor='#2C2F33')
        plt.close(self.env.figure)
        self.env.embed.set_image(url='attachment://' + self.env.filename)
        self.env.embed.set_footer(text='Click on the image to zoom in.')


class Report:

    def __init__(self, bot: DCSServerBot, plugin: str, filename: str):
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.env = ReportEnv(bot)
        default = f'./plugins/{plugin}/reports/{filename}'
        overwrite = f'./reports/{plugin}/{filename}'
        if not path.exists(default):
            raise FileNotFoundError(default)
        if path.exists(overwrite) and (path.getctime(overwrite) > path.getctime(default)):
            filename = overwrite
        else:
            filename = default
        with open(filename) as file:
            self.report_def = json.load(file)

    @staticmethod
    def format_string(string_: str, default_: Optional[str] = None, **kwargs) -> str:
        class NoneFormatter(string.Formatter):
            def format_field(self, value, spec):
                if value is None:
                    if default_:
                        value = default_
                    else:
                        raise KeyError
                return super().format_field(value, spec)
        try:
            string_ = NoneFormatter().format(string_, **kwargs)
        except KeyError:
            string_ = ''
        return string_

    @staticmethod
    def parse_input(kwargs: dict, params: List[Any]):
        new_args = kwargs.copy()
        for param in params:
            if param['name'] in new_args:
                if 'range' in param:
                    value = new_args[param['name']] or ''
                    if value not in param['range']:
                        raise ValueNotInRange(param['name'], value, param['range'])
                elif 'value' in param:
                    new_args[param['name']] = param['value']
            elif 'value' in param:
                new_args[param['name']] = param['value']
            elif 'default' in param:
                new_args[param['name']] = param['default']
        return new_args

    @staticmethod
    def parse_params(kwargs: dict, params: Tuple[dict, List]):
        new_args = kwargs.copy()
        if isinstance(params, dict):
            for key, value in params.items():
                new_args[key] = value
        else:
            new_args['params'] = params
        return new_args

    def render(self, *args, **kwargs) -> ReportEnv:
        # parse report parameters
        if 'input' in self.report_def:
            self.env.params = self.parse_input(kwargs, self.report_def['input'])
        else:
            self.env.params = kwargs.copy()
        # format the embed
        if 'color' in self.report_def:
            self.env.embed = discord.Embed(color=getattr(discord.Color, self.report_def['color'])())
        else:
            self.env.embed = discord.Embed()
        if 'title' in self.report_def:
            self.env.embed.title = self.format_string(self.report_def['title'], **self.env.params)
        if 'description' in self.report_def:
            self.env.embed.description = self.format_string(self.report_def['description'], **self.env.params)
        if 'url' in self.report_def:
            self.env.embed.url = self.report_def['url']
        if 'img' in self.report_def:
            self.env.embed.set_thumbnail(url=self.report_def['img'])

        for element in self.report_def['elements']:
            if isinstance(element, dict):
                if 'params' in element:
                    element_args = self.parse_params(self.env.params, element['params'])
                else:
                    element_args = self.env.params.copy()
                element_class = utils.str_to_class(element['class']) if 'class' in element else None
                if not element_class and 'type' in element:
                    element_class = getattr(sys.modules[__name__], element['type'])
            elif isinstance(element, str):
                element_class = getattr(sys.modules[__name__], element)
                element_args = self.env.params.copy()
            else:
                raise UnknownReportElement(str(element))
            if element_class:
                # remove parameters, that are not in the class __init__ signature
                signature = inspect.signature(element_class.__init__).parameters.keys()
                class_args = {name: value for name, value in element_args.items() if name in signature}
                element_class = element_class(self.env, **class_args)
                if isinstance(element_class, ReportElement):
                    # remove parameters, that are not in the render classes signature
                    signature = inspect.signature(element_class.render).parameters.keys()
                    render_args = {name: value for name, value in element_args.items() if name in signature}
                    element_class.render(**render_args)
                else:
                    raise UnknownReportElement(element['class'])
            else:
                raise ClassNotFound(element['class'])
        return self.env


class PaginationReport(Report):

    class NoPaginationInformation(Exception):
        pass

    def __init__(self, bot: DCSServerBot, ctx: Context, plugin: str, filename: str):
        super().__init__(bot, plugin, filename)
        self.ctx = ctx
        if 'pagination' not in self.report_def:
            raise PaginationReport.NoPaginationInformation

    def read_param(self, param: dict) -> Tuple[str, List]:
        name = param['name']
        values = None
        if 'sql' in param:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute(param['sql'])
                    values = list(x[0] for x in cursor.fetchall())
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)
        else:
            values = param['values']
        return name, values

    async def display(self, *args, **kwargs):
        name, values = self.read_param(self.report_def['pagination']['param'])

        async def pagination(value=None):
            try:
                try:
                    kwargs[name] = value
                    env = self.render(*args, **kwargs)
                    file = discord.File(env.filename) if env.filename else None
                    message = None
                    with suppress(Exception):
                        message = await self.ctx.send(embed=env.embed, file=file)
                    if env.filename:
                        os.remove(env.filename)
                except ValueNotInRange as ex:
                    await self.ctx.send(str(ex))
                except Exception as ex:
                    self.log.exception(ex)
                    await self.ctx.send('An error occurred. Please contact your Admin.')

                if message and (len(values) > 1):
                    await message.add_reaction('◀️')
                    await message.add_reaction('⏹️')
                    await message.add_reaction('▶️')
                    react = await utils.wait_for_single_reaction(self, self.ctx, message)
                    if value is None:
                        prev = values[-1]
                        nxt = values[0]
                    else:
                        i = 0
                        prev = nxt = None
                        for s in values:
                            if s == value:
                                break
                            i += 1
                        if i < len(values) - 1:
                            nxt = values[i + 1]
                        if i > 0:
                            prev = values[i - 1]

                    if react.emoji == '◀️':
                        await message.delete()
                        await pagination(prev)
                    elif react.emoji == '⏹️':
                        raise asyncio.TimeoutError
                    elif react.emoji == '▶️':
                        await message.delete()
                        await pagination(nxt)
            except asyncio.TimeoutError:
                await message.clear_reactions()

        await pagination()

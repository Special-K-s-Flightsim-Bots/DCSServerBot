from __future__ import annotations

import asyncio
import discord
import inspect
import json
import os
import psycopg
import sys

from abc import ABC, abstractmethod
from core import utils, Channel
from discord import Interaction, SelectOption
from discord.ui import View, Button, Select, Item
from discord.utils import MISSING
from typing import Optional, TYPE_CHECKING, Any, cast, Union

from .elements import ReportElement
from .env import ReportEnv
from .errors import UnknownReportElement, ClassNotFound
from .__utils import parse_input, parse_params

if TYPE_CHECKING:
    from core import Server
    from services import DCSServerBot

__all__ = [
    "Report",
    "Pagination",
    "PaginationReport",
    "PersistentReport"
]


class Report:

    def __init__(self, bot: DCSServerBot, plugin: str, filename: str):
        self.bot = bot
        self.log = bot.log
        self.apool = bot.apool
        self.env = ReportEnv(bot)
        default = f'./plugins/{plugin}/reports/{filename}'
        overwrite = f'./reports/{plugin}/{filename}'
        if os.path.exists(overwrite):
            self.filename = overwrite
        elif os.path.exists(default):
            self.filename = default
        else:
            raise FileNotFoundError(filename)
        with open(self.filename, mode='r', encoding='utf-8') as file:
            self.report_def = json.load(file)

    async def render(self, *args, **kwargs) -> ReportEnv:
        if 'input' in self.report_def:
            self.env.params = await parse_input(self, kwargs, self.report_def['input'])
        else:
            self.env.params = kwargs.copy()
        # add the bot to be able to access the whole environment from inside the report
        self.env.params['bot'] = self.bot
        # format the embed
        if 'color' in self.report_def:
            self.env.embed = discord.Embed(color=getattr(discord.Color, self.report_def.get('color', 'blue'))())
        else:
            self.env.embed = discord.Embed()
        for name, item in self.report_def.items():
            # parse report parameters
            if name == 'title':
                self.env.embed.title = utils.format_string(item, **self.env.params)[:256]
            elif name == 'description':
                self.env.embed.description = utils.format_string(item, **self.env.params)[:4096]
            elif name == 'url':
                self.env.embed.url = utils.format_string(item, **self.env.params)
            elif name == 'img':
                self.env.embed.set_thumbnail(url=utils.format_string(item, **self.env.params))
            elif name == 'footer':
                footer = self.env.embed.footer.text or ''
                text = utils.format_string(item, **self.env.params)
                if footer is None:
                    footer = text
                else:
                    footer += '\n' + text
                self.env.embed.set_footer(text=footer[:2048])
            elif name == 'elements':
                for element in item:
                    if isinstance(element, dict):
                        if 'params' in element:
                            element_args = parse_params(self.env.params, element['params'])
                        else:
                            element_args = self.env.params.copy()
                        element_class = utils.str_to_class(element['class']) if 'class' in element else None
                        if not element_class and 'type' in element:
                            element_class = getattr(sys.modules['core.report.elements'], element['type'])
                    elif isinstance(element, str):
                        element_class = getattr(sys.modules['core.report.elements'], element)
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
                            try:
                                await element_class.render(**render_args)
                            except (TimeoutError, asyncio.TimeoutError):
                                self.log.error(f"Timeout while processing report {self.filename}! "
                                               f"Some elements might be empty.")
                            except psycopg.OperationalError:
                                self.log.error(f"Database error while processing report {self.filename}! "
                                               f"Some elements might be empty.")
                            except Exception:
                                self.log.error(f"Error while processing report {self.filename}! "
                                               f"Some elements might be empty.", exc_info=True)
                        else:
                            raise UnknownReportElement(element['class'])
                    else:
                        raise ClassNotFound(element['class'])
        return self.env


class Pagination(ABC):
    def __init__(self, env: ReportEnv):
        self.env = env

    @abstractmethod
    async def values(self, **kwargs) -> list[Any]:
        ...


class PaginationReport(Report):

    class NoPaginationInformation(Exception):
        ...

    def __init__(self, interaction: discord.Interaction, plugin: str, filename: str,
                 pagination: Optional[list] = None, keep_image: bool = False):
        super().__init__(interaction.client, plugin, filename)
        self.interaction = interaction
        self.pagination = pagination
        self.keep_image = keep_image
        if 'pagination' not in self.report_def:
            raise PaginationReport.NoPaginationInformation

    async def read_param(self, param: dict, **kwargs) -> tuple[str, list]:
        name = param['name']
        values = None
        if 'sql' in param:
            async with self.apool.connection() as conn:
                values = [x[0] async for x in await conn.execute(param['sql'], kwargs)]
        elif 'values' in param:
            values = param['values']
        elif 'obj' in param:
            obj = kwargs[param['obj']]
            if isinstance(obj, list):
                values = obj
            elif isinstance(obj, dict):
                values = obj.keys()
        elif 'class' in param:
            values = await cast(Pagination, utils.str_to_class(param['class'])(self.env)).values(**kwargs)
        elif self.pagination:
            values = self.pagination
        return name, values

    class PaginationReportView(View):
        def __init__(self, name, values, index, func, keep_image: bool, *args, **kwargs):
            super().__init__()
            self.name = name
            self.values = values
            self.index = index
            self.func = func
            self.keep_image = keep_image
            self.args = args
            self.kwargs = kwargs
            select: Select = cast(Select, self.children[0])
            self.formatter = kwargs.get('formatter')
            if self.formatter:
                select.options = [SelectOption(label=self.formatter(x) or 'All',
                                               value=str(idx),
                                               default=(x is None)
                                               ) for idx, x in enumerate(self.values) if idx < 25]
            else:
                select.options = [SelectOption(label=x or 'All',
                                               value=str(idx),
                                               default=(x is None)
                                               ) for idx, x in enumerate(self.values) if idx < 25]
            if self.index == 0:
                target_children = self.children[1:3]
                new_states = [True, True]
            elif self.index == len(values) - 1:
                target_children = self.children[3:5]
                new_states = [True, True]
            else:
                target_children = []
                new_states = []
            for child, new_state in zip(target_children, new_states):
                child.disabled = new_state

        async def render(self, value) -> ReportEnv:
            self.kwargs[self.name] = value if value != 'All' else None
            return await self.func(*self.args, **self.kwargs)

        async def paginate(self, value, interaction: discord.Interaction):
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            env = await self.render(value)
            try:
                target_children = self.children[1:5]
                if self.index == 0:
                    new_states = [True, True, False, False]
                elif self.index == len(self.values) - 1:
                    new_states = [False, False, True, True]
                else:
                    new_states = [False, False, False, False]
                for child, state in zip(target_children, new_states):
                    child.disabled = state
                if env.filename:
                    await interaction.edit_original_response(embed=env.embed, view=self, attachments=[
                            discord.File(fp=env.buffer or env.filename, filename=os.path.basename(env.filename))
                        ]
                    )
                else:
                    await interaction.edit_original_response(embed=env.embed, view=self, attachments=[])
            finally:
                if not self.keep_image and env.filename:
                    if env.buffer:
                        env.buffer.close()
                    env.filename = None

        @discord.ui.select()
        async def callback(self, interaction: Interaction, select: Select):
            self.index = int(select.values[0])
            await self.paginate(self.values[self.index], interaction)

        @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary)
        async def on_start(self, interaction: Interaction, _: Button):
            self.index = 0
            await self.paginate(self.values[self.index], interaction)

        @discord.ui.button(label="Back", style=discord.ButtonStyle.primary)
        async def on_left(self, interaction: Interaction, _: Button):
            self.index -= 1
            await self.paginate(self.values[self.index], interaction)

        @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
        async def on_right(self, interaction: Interaction, _: Button):
            self.index += 1
            await self.paginate(self.values[self.index], interaction)

        @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary)
        async def on_end(self, interaction: Interaction, _: Button):
            self.index = len(self.values) - 1
            await self.paginate(self.values[self.index], interaction)

        @discord.ui.button(label="Quit", style=discord.ButtonStyle.red)
        async def on_cancel(self, interaction: Interaction, _: Button):
            self.index = -1
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            self.stop()

        async def on_error(self, interaction: Interaction, error: Exception, item: Item[Any], /) -> None:
            print(error)
            self.stop()

    async def render(self, *args, **kwargs) -> ReportEnv:
        # noinspection PyUnresolvedReferences
        if not self.interaction.response.is_done():
            # noinspection PyUnresolvedReferences
            await self.interaction.response.defer()
        name, values = await self.read_param(self.report_def['pagination']['param'], **kwargs)
        start_index = 0
        if 'start_index' in kwargs:
            start_index = kwargs['start_index']
        elif name in kwargs:
            if kwargs[name] in values:
                start_index = values.index(kwargs[name])
            elif kwargs[name] or len(values) != 1:
                values.insert(0, kwargs[name])
        elif len(values) == 0:
            values = [None]
        func = super().render

        message = None
        if len(values) > 1:
            view = self.PaginationReportView(name, values, start_index, func, self.keep_image, *args, **kwargs)
            env = await view.render(values[start_index])
        else:
            view = None
            kwargs[name] = values[0]
            env = await func(*args, **kwargs)
        try:
            try:
                message = await self.interaction.followup.send(
                    embed=env.embed,
                    view=view or MISSING,
                    file=discord.File(fp=env.buffer or env.filename,
                                      filename=os.path.basename(env.filename)) if env.filename else MISSING
                )
            finally:
                if not self.keep_image and env.filename:
                    if env.buffer:
                        env.buffer.close()
                    env.filename = None
            if view:
                await view.wait()
            else:
                message = None
        except Exception:
            self.log.error(f"Exception while processing report {self.filename}!")
            raise
        finally:
            try:
                if message:
                    await message.delete()
            except discord.NotFound:
                pass
        return self.env


class PersistentReport(Report):

    def __init__(self, bot: DCSServerBot, plugin: str, filename: str, *, embed_name: str,
                 channel_id: Optional[Union[Channel, int]] = Channel.STATUS, server: Optional[Server] = None):
        super().__init__(bot, plugin, filename)
        self.server = server
        self.embed_name: str = embed_name
        self.channel_id: Union[Channel, int] = channel_id

    async def render(self, *args, **kwargs) -> ReportEnv:
        env = None
        try:
            env = await super().render(*args, **kwargs)
            file = discord.File(fp=env.buffer or env.filename,
                                filename=os.path.basename(env.filename)) if env.filename else MISSING
            await self.bot.setEmbed(embed_name=self.embed_name, embed=env.embed, channel_id=self.channel_id,
                                    file=file, server=self.server)
            return env
        except Exception:
            self.log.error(f"Exception while processing report {self.filename}!")
            raise
        finally:
            if env and env.filename:
                env.buffer.close()
                env.filename = None

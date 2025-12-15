from __future__ import annotations

import asyncio
import discord
import inspect
import json
import logging
import os
import psycopg
import sys

from abc import ABC, abstractmethod
from contextlib import suppress
from core import utils, Channel
from discord import Interaction, SelectOption, ButtonStyle
from discord.ui import View, Button, Select, Item
from discord.utils import MISSING
from typing import TYPE_CHECKING, Any, cast

from .elements import ReportElement
from .env import ReportEnv
from .errors import UnknownReportElement, ClassNotFound
from .__utils import parse_input, parse_params

if TYPE_CHECKING:
    from core import Server
    from services.bot import DCSServerBot

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
        self.filename, self.report_def = self.load_report_def(plugin, filename)

    def load_report_def(self, plugin: str, filename: str):
        default = f'./plugins/{plugin}/reports/{filename}'
        overwrite = f'./reports/{plugin}/{filename}'
        if os.path.exists(overwrite):
            filename = overwrite
        elif os.path.exists(default):
            filename = default
        else:
            raise FileNotFoundError(filename)
        with open(filename, mode='r', encoding='utf-8') as file:
            report_def = json.load(file)
        if 'include' in report_def:
            report_def |= self.load_report_def(report_def['include'].get('plugin', plugin),
                                               report_def['include']['filename'])[1]
        else:
            for idx, element in enumerate(report_def.get('elements', [])):
                if 'include' in element:
                    report_def['elements'][idx] = (
                        self.load_report_def(element['include'].get('plugin', plugin), element['include']['filename'])
                    )[1]
        return filename, report_def

    async def render(self, *args, **kwargs) -> ReportEnv:
        # Cache the `report_def` locally for faster lookups and readability
        report_def = self.report_def
        env = self.env

        # Parse input parameters or copy kwargs
        if 'input' in report_def:
            env.params = await parse_input(self, kwargs, report_def['input'])
        else:
            env.params = kwargs.copy()

        # Add bot reference to params
        env.params['bot'] = self.bot

        # Create an embed with optional color
        embed_color = getattr(discord.Color, utils.format_string(report_def.get('color', 'blue'), **env.params),
                              discord.Color.blue)()
        env.embed = discord.Embed(color=embed_color)

        # Predefine keys that need formatting and apply transformations
        formatted_keys = {
            'title': {'max_length': 256, 'setter': lambda val: setattr(env.embed, 'title', val)},
            'description': {'max_length': 4096, 'setter': lambda val: setattr(env.embed, 'description', val)},
            'author': {'max_length': 256, 'setter': lambda val: env.embed.set_author(
                name=val, url=env.embed.author.url, icon_url=env.embed.author.icon_url)
            },
            'author_url': {'setter': lambda val: env.embed.set_author(
                name=env.embed.author.name, url=val, icon_url=env.embed.author.icon_url)
            },
            'author_icon': {'setter': lambda val: env.embed.set_author(
                name=env.embed.author.name, url=env.embed.author.url, icon_url=val)
            },
            'url': {'setter': lambda val: setattr(env.embed, 'url', val)},
            'img': {'setter': lambda val: env.embed.set_thumbnail(url=val)},
            'footer': {
                'setter': lambda val: env.embed.set_footer(
                    text=f"{env.embed.footer.text or ''}\n{val}"[:2048]
                )
            },
        }

        for key, config in formatted_keys.items():
            if value := report_def.get(key):
                formatted_value = utils.format_string(value, **env.params)
                if 'max_length' in config:
                    formatted_value = formatted_value[:config['max_length']]
                config['setter'](formatted_value)

        # Process mentions
        if mention := report_def.get('mention'):
            if isinstance(mention, int):
                env.mention = f"<@&{mention}>"
            else:
                env.mention = ''.join([f"<@&{x}>" for x in mention])

        # Handle the 'elements' section
        if elements := report_def.get('elements'):
            for element in elements:
                await self._process_element(element, env.params)

        return env

    async def _process_element(self, element, params):
        """
        Helper function to process individual elements sequentially.
        """
        # Resolve the element's class and arguments
        element_class, element_args = self._resolve_element_class_and_args(element, params)

        if not element_class:
            return  # Skip if the class couldn't be resolved

        # Filter arguments for the __init__ method
        init_args = self._filter_args(element_args, element_class.__init__)
        instance = element_class(self.env, **init_args)

        if not isinstance(instance, ReportElement):
            raise UnknownReportElement(element.get('class', str(element)))

        # Filter arguments for the render method
        render_args = self._filter_args(element_args, instance.render)

        # Render the element and handle exceptions
        try:
            await instance.render(**render_args)
        except (TimeoutError, asyncio.TimeoutError):
            self.log.error(f"Timeout while processing report {self.filename}! Some elements might be empty.")
        except psycopg.OperationalError:
            self.log.error(f"Database error while processing report {self.filename}! Some elements might be empty.")
        except Exception:
            self.log.error(f"Error while processing report {self.filename}! Some elements might be empty.",
                           exc_info=True)
            raise

    @staticmethod
    def _resolve_element_class_and_args(element, params):
        """
        Resolves the class and arguments for a given element.
        """
        if isinstance(element, dict):
            element_args = parse_params(params, element.get('params', params.copy()))
            class_name = element.get('class') or element.get('type')
            element_class = None

            # Dynamically retrieve the class instance
            if class_name:
                element_class = (
                    utils.str_to_class(class_name)
                    if 'class' in element
                    else getattr(sys.modules['core.report.elements'], class_name, None)
                )
        elif isinstance(element, str):
            element_class = getattr(sys.modules['core.report.elements'], element, None)
            element_args = params.copy()
        else:
            raise UnknownReportElement(str(element))

        if not element_class:
            raise ClassNotFound(str(element.get('class', element)))

        return element_class, element_args

    @staticmethod
    def _filter_args(args, method):
        """
        Filters arguments based on a method's signature, ensuring compatibility.
        """
        signature = inspect.signature(method).parameters
        return {name: value for name, value in args.items() if name in signature}


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
                 pagination: list | None = None, keep_image: bool = False):
        super().__init__(interaction.client, plugin, filename)
        self.interaction = interaction
        self.pagination = pagination
        self.keep_image = keep_image
        if 'pagination' not in self.report_def:
            raise PaginationReport.NoPaginationInformation

    async def read_param(self, param: dict, **kwargs) -> tuple[str, list[str]]:
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
            self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
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

        async def render(self, value: str) -> ReportEnv:
            self.kwargs[self.name] = value if value != 'All' else None
            return await self.func(*self.args, **self.kwargs)

        async def paginate(self, value: str, interaction: discord.Interaction):
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

        # noinspection PyTypeChecker
        @discord.ui.button(label="<<", style=ButtonStyle.secondary)
        async def on_start(self, interaction: Interaction, _: Button):
            self.index = 0
            await self.paginate(self.values[self.index], interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label="Back", style=ButtonStyle.primary)
        async def on_left(self, interaction: Interaction, _: Button):
            self.index -= 1
            await self.paginate(self.values[self.index], interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label="Next", style=ButtonStyle.primary)
        async def on_right(self, interaction: Interaction, _: Button):
            self.index += 1
            await self.paginate(self.values[self.index], interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label=">>", style=ButtonStyle.secondary)
        async def on_end(self, interaction: Interaction, _: Button):
            self.index = len(self.values) - 1
            await self.paginate(self.values[self.index], interaction)

        # noinspection PyTypeChecker
        @discord.ui.button(label="Quit", style=ButtonStyle.red)
        async def on_cancel(self, interaction: Interaction, _: Button):
            self.index = -1
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            self.stop()

        async def on_error(self, interaction: Interaction, error: Exception, item: Item[Any], /) -> None:
            self.log.exception(error)
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
                    env.mention,
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
            return self.env
        except Exception:
            self.log.error(f"Exception while processing report {self.filename}!")
            raise
        finally:
            if message:
                with suppress(discord.NotFound):
                    await message.delete()

class PersistentReport(Report):

    def __init__(self, bot: DCSServerBot, plugin: str, filename: str, *, embed_name: str,
                 channel_id: Channel | int | None = Channel.STATUS, server: Server | None = None):
        super().__init__(bot, plugin, filename)
        self.server = server
        self.embed_name: str = embed_name
        self.channel_id: Channel | int = channel_id

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
            msg = f"Exception while processing report {self.filename}!"
            if self.server:
                msg += f' for server {self.server.name}'
            self.log.error(msg, exc_info=True)
            raise
        finally:
            if env and env.filename:
                env.buffer.close()
                env.filename = None

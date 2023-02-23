import discord
import json
import os
import psycopg2
import shutil
import time
from contextlib import closing
from copy import deepcopy
from core import Plugin, DCSServerBot, PluginRequiredError, utils, PaginationReport, Report, Server, TEventListener
from datetime import datetime
from discord import SelectOption, TextStyle
from discord.ext import commands, tasks
from discord.ui import View, Select, Modal, TextInput, Item
from os import path
from typing import Optional, Union, List, Type, Any
from .listener import GreenieBoardEventListener


class GreenieBoardAgent(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.auto_delete.start()

    async def cog_unload(self):
        self.auto_delete.cancel()

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server.installation == element['installation']) or \
                                ('server_name' in element and server.name == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                # we only specify the persistent channel in the server settings, if it is expicitely defined
                if 'persistent_channel' in default:
                    del default['persistent_channel']
                if default and not specific:
                    self._config[server.name] = default
                elif specific and not default:
                    self._config[server.name] = specific
                elif default and specific:
                    merged = default
                    # specific settings will overwrite default settings
                    for key, value in specific.items():
                        merged[key] = value
                    self._config[server.name] = merged
            else:
                return None
        return self._config[server.name] if server.name in self._config else None

    def migrate(self, version: str):
        if version != '1.3':
            return
        os.rename('config/greenieboard.json', 'config/greenieboard.bak')
        with open('config/greenieboard.bak') as infile:
            old: dict = json.load(infile)
        dirty = False
        for config in old['configs']:
            if 'ratings' in config and '---' in config['ratings']:
                config['ratings']['--'] = config['ratings']['---']
                del config['ratings']['---']
                dirty = True
        if dirty:
            with open('config/greenieboard.json', 'w') as outfile:
                json.dump(old, outfile, indent=2)
                self.log.info('  => config/greenieboard.json migrated to new format, please verify!')

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Greenieboard ...')
        with closing(conn.cursor()) as cursor:
            if ucids:
                for ucid in ucids:
                    cursor.execute('DELETE FROM greenieboard WHERE player_ucid = %s', (ucid,))
            elif days > 0:
                cursor.execute(f"DELETE FROM greenieboard WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Greenieboard pruned.')

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE message_persistence SET embed_name = %s '
                               'WHERE embed_name = %s AND server_name IN (%s, %s)',
                               (f'greenieboard-{new_name}', f'greenieboard-{old_name}', old_name, new_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Show carrier landing qualifications', usage='[member]', aliases=['traps'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def carrier(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        def format_landing(landing: dict) -> str:
            return f"{landing['time']:%y-%m-%d %H:%M:%S} - {landing['unit_type']}@{landing['place']}: {landing['grade']}"

        if not member:
            member = ctx.message.author
        if isinstance(member, discord.Member):
            ucid = self.bot.get_ucid_by_member(member)
            name = member.display_name
        else:
            name = member
            if len(params) > 0:
                name += ' ' + ' '.join(params)
            ucid, name = self.bot.get_ucid_by_name(name)
        landings = List[dict]
        num_landings = max(self.locals['configs'][0]['num_landings'], 25)
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute("SELECT id, p.name, g.grade, g.unit_type, g.comment, g.place, g.trapcase, g.wire, "
                               "g.time, g.points, g.trapsheet FROM greenieboard g, players p WHERE p.ucid = %s "
                               "AND g.player_ucid = p.ucid ORDER BY ID DESC LIMIT %s", (ucid, num_landings))
                if cursor.rowcount == 0:
                    await ctx.send('No carrier landings recorded for this user.',
                                   delete_after=timeout if timeout > 0 else None)
                    return
                landings = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        report = Report(self.bot, self.plugin_name, 'traps.json')
        env = await report.render(ucid=ucid, name=utils.escape_string(name))
        n = await utils.selection(ctx, embed=env.embed, placeholder="Select a trap for details",
                                  options=[
                                      SelectOption(label=format_landing(x), value=str(idx))
                                      for idx, x in enumerate(landings)
                                  ])
        if n:
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'lsoRating.json',
                                      timeout if timeout > 0 else None)
            await report.render(landings=landings, start_index=int(n), formatter=format_landing)
        await ctx.message.delete()

    @commands.command(description='Display the current greenieboard', usage='[num rows]', aliases=['greenie'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def greenieboard(self, ctx, num_rows: Optional[int] = 10):
        try:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'greenieboard.json',
                                      timeout if timeout > 0 else None)
            await report.render(server_name=None, num_rows=num_rows)
        finally:
            await ctx.message.delete()

    @tasks.loop(hours=24.0)
    async def auto_delete(self):
        def do_delete(path: str, days: int):
            now = time.time()
            for f in [os.path.join(path, x) for x in os.listdir(path)]:
                if os.stat(f).st_mtime < (now - days * 86400):
                    if os.path.isfile(f):
                        os.remove(f)

        for server in self.bot.servers.values():
            config = self.get_config(server)
            basedir = os.path.expandvars(self.bot.config[server.installation]['DCS_HOME'])
            if 'Moose.AIRBOSS' in config and 'delete_after' in config['Moose.AIRBOSS']:
                basedir += os.path.sep + config['Moose.AIRBOSS']['basedir'] if 'basedir' in config['Moose.AIRBOSS'] else ''
                do_delete(basedir, config['Moose.AIRBOSS']['delete_after'])
            elif 'FunkMan' in config and 'delete_after' in config['FunkMan']:
                basedir += os.path.sep + config['FunkMan']['basedir'] if 'basedir' in config['FunkMan'] else ''
                do_delete(basedir, config['FunkMan']['delete_after'])


class GreenieBoardMaster(GreenieBoardAgent):
    @commands.command(description='Adds a trap to the Greenieboard', usage='<@member|ucid>')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def add_trap(self, ctx: commands.Context, user: Union[discord.Member, str]):
        if isinstance(user, discord.Member):
            ucid = self.bot.get_ucid_by_member(user)
            if not ucid:
                await ctx.send(f'Member {user.display_name} is not linked.')
                return
        elif len(user) != 32:
            await ctx.send(f'Usage: {ctx.prefix}add_trap <@member|ucid>')
            return
        else:
            ucid = user

        config = self.locals['configs'][0]
        if 'ratings' not in config:
            await ctx.send('You need to specify ratings in your greenieboard.json to use add_trap!')
            return
        planes = ['AV8BNA', 'F-14A-135-GR', 'F-14B', 'FA-18C_hornet', 'Su-33']

        class TrapModal(Modal):
            time = TextInput(label='Time (HH24:MI)', style=TextStyle.short, required=True, min_length=5, max_length=5)
            case = TextInput(label='Case', style=TextStyle.short, required=True, min_length=1, max_length=1)
            grade = TextInput(label='Grade', style=TextStyle.short, required=True, min_length=1, max_length=4)
            comment = TextInput(label='LSO Comment', style=TextStyle.long, required=False)
            wire = TextInput(label='Wire', style=TextStyle.short, required=False, min_length=1, max_length=1)

            def __init__(self, bot: DCSServerBot, *, unit_type: str):
                super().__init__(title="Enter the trap details")
                self.bot = bot
                self.log = bot.log
                self.pool = bot.pool
                self.unit_type = unit_type
                self.success = False

            async def on_submit(self, interaction: discord.Interaction, /) -> None:
                await interaction.response.defer()
                time = datetime.strptime(self.time.value, '%H:%M').time()
                night = time.hour >= 20 or time.hour <= 6
                if self.case.value not in ['1', '2', '3']:
                    raise TypeError('Case needs to be one of 1, 2 or 3.')
                grade = self.grade.value.upper()
                if grade not in config['ratings'].keys():
                    raise ValueError("Grade has to be one of " + ', '.join([utils.escape_string(x) for x in config['ratings'].keys()]))
                if self.wire.value and self.wire.value not in ['1', '2', '3', '4']:
                    raise TypeError('Wire needs to be one of 1 to 4.')

                conn = self.pool.getconn()
                try:
                    with closing(conn.cursor()) as cursor:
                        cursor.execute('INSERT INTO greenieboard (player_ucid, unit_type, grade, comment, place, '
                                       'night, points, wire, trapcase) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                                       (ucid, self.unit_type, self.grade.value, self.comment.value, 'n/a', night,
                                        config['ratings'][grade], self.wire.value, self.case.value))
                    conn.commit()
                    self.success = True
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                    raise
                finally:
                    self.pool.putconn(conn)

            async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
                await interaction.followup.send(error)
                self.stop()

        class TrapView(View):

            def __init__(self, bot: DCSServerBot):
                super().__init__()
                self.bot = bot
                self.log = bot.log
                self.success = False

            @discord.ui.select(placeholder='Select the plane for the trap',
                               options=[SelectOption(label=x) for x in planes])
            async def callback(self, interaction: discord.Interaction, select: Select):
                modal = TrapModal(self.bot, unit_type=select.values[0])
                await interaction.response.send_modal(modal)
                await modal.wait()
                self.success = modal.success
                self.stop()

            async def on_error(self, interaction: discord.Interaction, error: Exception, item: Item[Any], /) -> None:
                await interaction.followup.send(error)
                self.stop()

        view = TrapView(self.bot)
        msg = await ctx.send(view=view)
        try:
            await view.wait()
            if view.success:
                await ctx.send('Trap added.')
            else:
                await ctx.send('Aborted.')
        finally:
            await msg.delete()


async def setup(bot: DCSServerBot):
    if 'missionstats' not in bot.plugins:
        raise PluginRequiredError('missionstats')
    # make sure that we have a proper configuration, take the default one if none is there
    if not path.exists('config/greenieboard.json'):
        bot.log.info('No greenieboard.json found, copying the sample.')
        shutil.copyfile('config/greenieboard.json.sample', 'config/greenieboard.json')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(GreenieBoardMaster(bot, GreenieBoardEventListener))
    else:
        await bot.add_cog(GreenieBoardAgent(bot, GreenieBoardEventListener))

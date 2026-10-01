import os
from typing import Literal

import discord
import psycopg

from configparser import ConfigParser

from discord import app_commands

from core import Plugin, PluginInstallationError, PluginConfigurationError, DEFAULT_TAG, Server, Group, utils, \
    get_translation
from services.bot import DCSServerBot

from .listener import FunkManEventListener

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

_ = get_translation(__name__.split('.')[1])


class FunkMan(Plugin[FunkManEventListener]):

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            raise PluginInstallationError(self.plugin_name,
                                          f"Can't find {self.node.config_dir}/plugins/funkman.yaml, "
                                          f"please create one!")
        path = config.get(DEFAULT_TAG, {}).get('install')
        if not path or not os.path.exists(path):
            raise PluginInstallationError(self.plugin_name,
                                          f"FunkMan install path is not set correctly in the DEFAULT-section of "
                                          f"your {self.plugin_name}.yaml! FunkMan will not work.")
        return config

    async def install(self) -> bool:
        if await super().install():
            config = self.get_config()
            if 'install' not in config:
                raise PluginConfigurationError(self.plugin_name, 'install')
            funkpath = os.path.expandvars(config['install'])
            if not os.path.exists(funkpath) or not os.path.exists(os.path.join(funkpath, 'FunkMan.ini')):
                self.log.error(f"No FunkMan installation found at {funkpath}!")
                raise PluginConfigurationError(self.plugin_name, 'install')
            if 'CHANNELID_MAIN' not in config:
                self.log.info('  => Migrating FunkMan.ini ...')
                ini = ConfigParser()
                ini.read(os.path.join(config['install'], 'FunkMan.ini'))
                if 'CHANNELID_MAIN' in ini['FUNKBOT']:
                    config['CHANNELID_MAIN'] = int(ini['FUNKBOT']['CHANNELID_MAIN'])
                if 'CHANNELID_RANGE' in ini['FUNKBOT']:
                    config['CHANNELID_RANGE'] = int(ini['FUNKBOT']['CHANNELID_RANGE'])
                if 'CHANNELID_AIRBOSS' in ini['FUNKBOT']:
                    config['CHANNELID_AIRBOSS'] = int(ini['FUNKBOT']['CHANNELID_AIRBOSS'])
                if 'IMAGEPATH' in ini['FUNKPLOT']:
                    if ini['FUNKPLOT']['IMAGEPATH'].startswith('.'):
                        config['IMAGEPATH'] = config['install'] + ini['FUNKPLOT']['IMAGEPATH'][1:]
                    else:
                        config['IMAGEPATH'] = ini['FUNKPLOT']['IMAGEPATH']
                with open(os.path.join(self.node.config_dir, 'plugins', 'funkman.yaml'), mode='w',
                          encoding='utf-8') as outfile:
                    yaml.dump({DEFAULT_TAG: config}, outfile)
            return True
        return False

    def get_config(self, server: Server | None = None, *, plugin_name: str | None = None,
                   use_cache: bool | None = True) -> dict:
        # retrieve the config from another plugin
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name] or not use_cache:
            default, specific = self.get_base_config(server)
            for x in ['strafe_board', 'strafe_channel', 'bomb_board', 'bomb_channel']:
                default.pop(x, None)
            self._config[server.node.name][server.instance.name] = default | specific
        return self._config[server.node.name][server.instance.name]

    async def prune(self, conn: psycopg.AsyncConnection, days: int) -> None:
        self.log.debug('Pruning FunkMan ...')
        await conn.execute(f"""
            DELETE FROM bomb_runs WHERE time < (DATE(now() AT TIME ZONE 'utc') - %s::interval)
        """, (f'{days} days', ))
        await conn.execute("""
            DELETE FROM strafe_runs WHERE time < (DATE(now() AT TIME ZONE 'utc') - %s::interval)
        """, (f'{days} days', ))
        self.log.debug('FunkMan pruned.')

    async def _wipe(self, interaction: discord.Interaction, what: Literal['bomb', 'strafe'],
                    user: str | discord.Member | None):
        ephemeral = utils.get_ephemeral(interaction)

        sql = f'DELETE FROM {what}_runs'
        if not user:
            message = _('Do you want to wipe the whole {} board?').format(what)
            ucid = None
        else:
            if isinstance(user, discord.Member):
                ucid = await self.bot.get_ucid_by_member(user)
                if not ucid:
                    await interaction.response.send_message(_('User {} is not linked!').format(user.display_name),
                                                            ephemeral=ephemeral)
                    return
            else:
                ucid = user
            message = _('Do you want to wipe all {} runs of user {}').format(
                what, user.display_name if isinstance(user, discord.Member) else user
            )
            sql += ' WHERE player_ucid = %(ucid)s'
        if not await utils.yn_question(interaction, message, ephemeral=ephemeral):
            await interaction.followup.send(_('Aborted'), ephemeral=ephemeral)
            return
        async with self.node.apool.connection() as conn:
            await conn.execute(sql, {"ucid": ucid})
        await interaction.followup.send(_('{} runs wiped.').format(what.title()), ephemeral=ephemeral)

    # New command group "/strafeboard"
    strafeboard = Group(name="strafeboard", description=_("Commands to manage strafe boards"))

    @strafeboard.command(description=_('Delete all traps'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def clear(self, interaction: discord.Interaction,
                    user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None):
        await self._wipe(interaction, 'strafe', user)

    # New command group "/bombboard"
    bombboard = Group(name="bombboard", description=_("Commands to manage bomb boards"))

    @bombboard.command(description=_('Delete all traps'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def clear(self, interaction: discord.Interaction,
                    user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None = None):
        await self._wipe(interaction, 'bomb', user)


async def setup(bot: DCSServerBot):
    await bot.add_cog(FunkMan(bot, FunkManEventListener))

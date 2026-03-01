import aiohttp
import discord
import luadata
import os

from core import Server
from discord import ButtonStyle, ChannelType
from discord.ui import Button, ChannelSelect
from io import BytesIO
from services.bot import DCSServerBot


class ServerModal(discord.ui.Modal):
    def __init__(self, title: str, config: dict) -> None:
        super().__init__(title=title)
        self.config = config


class MainModal(ServerModal):
    def __init__(self, config: dict) -> None:
        super().__init__(title="Main Config", config=config)
        server_name = self.config.get('name')
        if server_name == 'n/a':
            server_name = None
        else:
            server_name = server_name[:80]
        self.name = discord.ui.Label(
            text="Server Name",
            component=discord.ui.TextInput(
                placeholder="Your server name",
                default=server_name,
                style=discord.TextStyle.short,
                required=True
            )
        )
        self.add_item(self.name)
        self.description = discord.ui.Label(
            text="Description",
            component=discord.ui.TextInput(
                placeholder="Type a brief description of your server here.",
                default=self.config.get('description', '')[:2000],
                style=discord.TextStyle.long,
                required=False
            )
        )
        self.add_item(self.description)
        self.password = discord.ui.Label(
            text="Password",
            description="(leave blank to disable)",
            component=discord.ui.TextInput(
                default=self.config.get('password', '')[:80],
                style=discord.TextStyle.short,
                required=False
            )
        )
        self.add_item(self.password)
        self.port = discord.ui.Label(
            text="Server Port",
            component=discord.ui.TextInput(
                default=self.config.get('port', '10308'),
                style=discord.TextStyle.short,
                max_length=5,
                required=True
            )
        )
        self.add_item(self.port)
        self.max_players = discord.ui.Label(
            text="Maximum number of players",
            component=discord.ui.TextInput(
                placeholder="Maximum number of players",
                default=self.config.get('maxPlayers', '16'),
                style=discord.TextStyle.short,
                max_length=3,
                required=True
            )
        )
        self.add_item(self.max_players)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        self.config['name'] = self.name.component.value
        self.config['description'] = self.description.component.value
        self.config['password'] = self.password.component.value
        self.config['port'] = int(self.port.component.value)
        self.config['maxPlayers'] = int(self.max_players.component.value)


class SetupModal(ServerModal):
    def __init__(self, config: dict) -> None:
        super().__init__(title="Server Setup", config=config)
        self.resume_mode = discord.ui.Label(
            text="Resume Mode",
            component=discord.ui.Select(
                options=[
                    discord.SelectOption(label="Pause without Clients", value="2",
                                         default=self.config.get('advanced', {}).get('resume_mode') == 2),
                    discord.SelectOption(label="Always Run", value="1",
                                         default=self.config.get('advanced', {}).get('resume_mode') == 1),
                    discord.SelectOption(label="Pause on Load", value="0",
                                         default=self.config.get('advanced', {}).get('resume_mode', 0) == 0)
                ]
            )
        )
        self.add_item(self.resume_mode)
        self.max_ping = discord.ui.Label(
            text="Maximum allowed Ping",
            component=discord.ui.TextInput(
                placeholder="Values above 300 tend to cause lags / desyncs",
                default=str(self.config.get('advanced', {}).get('maxPing', '0')),
                style=discord.TextStyle.short,
                max_length=3,
                required=False
            )
        )
        self.add_item(self.max_ping)
        self.public = discord.ui.Label(
            text="Public Server",
            component=discord.ui.Checkbox(
                default=self.config.get('isPublic', True)
            )
        )
        self.add_item(self.public)
        self.can_screenshot = discord.ui.Label(
            text="Server can Screenshot",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('server_can_screenshot', False)
            )
        )
        self.add_item(self.can_screenshot)
        self.allow_trial = discord.ui.Label(
            text="Allow Trial-only Clients",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_trial_only_clients', True)
            )
        )
        self.add_item(self.allow_trial)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        self.config.setdefault('advanced', {}).update({
            'resume_mode': int(self.resume_mode.component.values[0]),
            'maxPing': int(self.max_ping.component.value),
            "server_can_screenshot": self.can_screenshot.component.value,
            "allow_trial_only_clients": self.allow_trial.component.value,
        })
        self.config['isPublic'] = self.public.component.value


class RequirementsModal(ServerModal):
    def __init__(self, config: dict) -> None:
        super().__init__(title="Requirements", config=config)
        self.pure_clients = discord.ui.Label(
            text="Require Pure Clients",
            component=discord.ui.Checkbox(
                default=self.config.get('require_pure_clients', False)
            )
        )
        self.add_item(self.pure_clients)
        self.pure_scripts = discord.ui.Label(
            text="Require Pure Scripts",
            component=discord.ui.Checkbox(
                default=self.config.get('require_pure_scripts', False)
            )
        )
        self.add_item(self.pure_scripts)
        self.pure_models = discord.ui.Label(
            text="Require Pure Models",
            component=discord.ui.Checkbox(
                default=self.config.get('require_pure_models', False)
            )
        )
        self.add_item(self.pure_models)
        self.pure_textures = discord.ui.Label(
            text="Require Pure Textures",
            component=discord.ui.Checkbox(
                default=self.config.get('require_pure_textures', False)
            )
        )
        self.add_item(self.pure_textures)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        self.config['require_pure_clients'] = self.pure_clients.component.value
        self.config['require_pure_scripts'] = self.pure_scripts.component.value
        self.config['require_pure_models'] = self.pure_models.component.value
        self.config['require_pure_textures'] = self.pure_textures.component.value


class RestrictionsModal(ServerModal):
    def __init__(self, config: dict) -> None:
        super().__init__(title="Restrictions", config=config)
        self.allow_change_tailno = discord.ui.Label(
            text="Allow Change Tail-No.",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_change_tailno', True)
            )
        )
        self.add_item(self.allow_change_tailno)
        self.allow_dynamic_radio = discord.ui.Label(
            text="Allow Dynamic Radio",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_dynamic_radio', True)
            )
        )
        self.add_item(self.allow_dynamic_radio)
        self.allow_change_skin = discord.ui.Label(
            text="Allow Change Skin",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_change_skin', True)
            )
        )
        self.add_item(self.allow_change_skin)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        self.config.setdefault('advanced', {}).update({
            'allow_change_tailno': self.allow_change_tailno.component.value,
            'allow_dynamic_radio': self.allow_dynamic_radio.component.value,
            "allow_change_skin": self.allow_change_skin.component.value
        })


class AntiCheatModal(ServerModal):
    def __init__(self, config: dict) -> None:
        super().__init__(title="Anti Cheat", config=config)
        self.allow_object_export = discord.ui.Label(
            text="Allow Object Export",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_object_export', True)
            )
        )
        self.add_item(self.allow_object_export)
        self.allow_sensor_export = discord.ui.Label(
            text="Allow Sensor Export",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_sensor_export', True)
            )
        )
        self.add_item(self.allow_sensor_export)
        self.allow_ownship_export = discord.ui.Label(
            text="Allow Ownship Export",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_ownship_export', True)
            )
        )
        self.add_item(self.allow_ownship_export)
        self.allow_players_pool = discord.ui.Label(
            text="Allow Players Pool",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('allow_players_pool', True)
            )
        )
        self.add_item(self.allow_players_pool)
        self.disable_events = discord.ui.Label(
            text="Disable all Events",
            component=discord.ui.Checkbox(
                default=self.config.get('advanced', {}).get('disable_events', True)
            )
        )
        self.add_item(self.disable_events)

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        self.config.setdefault('advanced', {}).update({
            'allow_object_export': self.allow_object_export.component.value,
            'allow_sensor_export': self.allow_sensor_export.component.value,
            "allow_ownship_export": self.allow_ownship_export.component.value,
            "allow_players_pool": self.allow_players_pool.component.value,
            "disable_events": self.disable_events.component.value
        })


class UploadModal(discord.ui.Modal):
    file = discord.ui.Label(
        text="Upload File",
        component=discord.ui.FileUpload(
            max_values=1,
            required=True
        )
    )

    def __init__(self, config: dict) -> None:
        super().__init__(title="Upload File")
        self.config = config

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await interaction.response.defer()
        file: discord.Attachment = self.file.component.values[0]
        if file:
            node = interaction.client.node
            async with aiohttp.ClientSession() as session:
                async with session.get(file.url, proxy=node.proxy, proxy_auth=node.proxy_auth) as response:
                    response.raise_for_status()
                    data = luadata.unserialize(await response.read(), encoding='utf-8')
                    for key, value in data.items():
                        self.config[key] = value


class ConfigView(discord.ui.View):
    def __init__(self, bot: DCSServerBot, server: Server):
        super().__init__()
        self.bot = bot
        self.server = server
        self.config = server.settings.copy()
        self.cancelled = False
        self.channel_update = False
        if self.bot.locals.get('channels', {}).get('admin'):
            # noinspection PyUnresolvedReferences
            self.children[0].disabled = True
        # noinspection PyUnresolvedReferences
        self.children[8].disabled = True
        self.toggle_config()

    def render(self) -> discord.Embed:
        embed = discord.Embed(colour=discord.Colour.blue())
        embed.title = "Server Configuration"
        embed.description = f"Configure settings for {self.server.display_name}"
        embed.add_field(name="Setup I", value="Name, Description, Password, Port, ...")
        embed.add_field(name="Setup II", value="Resume Mode, Ping, ...")
        embed.add_field(name='_ _', value='_ _')
        embed.add_field(name="Require", value="Client Requirements")
        embed.add_field(name="Restrict", value="Tail-no and Skin changes, ...")
        embed.add_field(name='_ _', value='_ _')
        embed.add_field(name="Anti-Cheat", value="Safety Settings")
        embed.set_footer(text="Press Save to write a new serverSettings.lua file.")
        return embed

    def toggle_ok(self) -> bool:
        if self.config['name'] != 'n/a':
            # noinspection PyUnresolvedReferences
            self.children[8].disabled = False
            return True
        else:
            # noinspection PyUnresolvedReferences
            self.children[8].disabled = True
            return False

    def toggle_config(self) -> bool:
        try:
            if (self.server.locals.get('channels', {}).get('admin', self.bot.locals.get('channels', {}).get('admin', -1)) != -1
                    and self.server.locals.get('channels', {}).get('status', -1) != -1):
                # noinspection PyUnresolvedReferences
                self.children[3].disabled = False
                if self.server.name != 'n/a':
                    for i in range(4, 8):
                        # noinspection PyUnresolvedReferences
                        self.children[i].disabled = False
                self.toggle_ok()
                return True
            else:
                for i in range(3, 8):
                    # noinspection PyUnresolvedReferences
                    self.children[i].disabled = True
                return False
        except Exception as ex:
            self.bot.log.exception(ex)
            return False

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[ChannelType.text],
        placeholder="Select an admin channel",
        row=0
    )
    async def admin_channel(self, interaction: discord.Interaction,
                            select: ChannelSelect[discord.TextChannel]) -> None:
        await interaction.response.defer()
        if 'channels' not in self.server.locals:
            self.server.locals['channels'] = {}
        self.server.locals['channels']['admin'] = select.values[0].id
        self.channel_update = True
        if self.toggle_config():
            await interaction.edit_original_response(view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[ChannelType.text],
        placeholder="Select a status channel",
        row=1
    )
    async def status_channel(self, interaction: discord.Interaction,
                             select: ChannelSelect[discord.TextChannel]) -> None:
        await interaction.response.defer()
        if 'channels' not in self.server.locals:
            self.server.locals['channels'] = {}
        self.server.locals['channels']['status'] = select.values[0].id
        self.channel_update = True
        if self.toggle_config():
            await interaction.edit_original_response(view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[ChannelType.text],
        placeholder="Select a chat channel (optional)",
        row=2
    )
    async def chat_channel(self, interaction: discord.Interaction, select: ChannelSelect[discord.TextChannel]) -> None:
        await interaction.response.defer()
        if 'channels' not in self.server.locals:
            self.server.locals['channels'] = {}
        self.server.locals['channels']['chat'] = select.values[0].id
        self.channel_update = True

    @discord.ui.button(label='Setup I', style=ButtonStyle.primary, row=3)
    async def on_main(self, interaction: discord.Interaction, _: Button):
        modal = MainModal(self.config)
        await interaction.response.send_modal(modal)
        await modal.wait()
        if self.server.name == 'n/a' and self.config['name'] != 'n/a':
            self.toggle_ok()
            for i in range(4, 8):
                # noinspection PyUnresolvedReferences
                self.children[i].disabled = False
            await interaction.edit_original_response(view=self)

    @discord.ui.button(label='Setup II', style=ButtonStyle.primary, row=3)
    async def on_setup(self, interaction: discord.Interaction, _: Button):
        modal = SetupModal(self.config)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='Require', style=ButtonStyle.primary, row=3)
    async def on_requirements(self, interaction: discord.Interaction, _: Button):
        modal = RequirementsModal(self.config)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='Restrict', style=ButtonStyle.primary, row=3)
    async def on_restrictions(self, interaction: discord.Interaction, _: Button):
        modal = RestrictionsModal(self.config)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='Anti-Cheat', style=ButtonStyle.primary, row=3)
    async def on_anti_cheat(self, interaction: discord.Interaction, _: Button):
        modal = AntiCheatModal(self.config)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='Save', style=ButtonStyle.green, emoji='💾', row=4)
    async def on_save(self, interaction: discord.Interaction, _: Button):
        await interaction.response.defer()
        old_name = self.server.name
        new_name = self.config['name']
        if old_name != new_name:
            await self.server.rename(new_name=new_name, update_settings=True)
            interaction.client.servers[new_name] = self.server
            if old_name in interaction.client.servers:
                del interaction.client.servers[old_name]
        for key, value in self.config.items():
            self.server.settings[key] = value
        self.stop()

    @discord.ui.button(label='Download', style=ButtonStyle.primary, emoji='📤', row=4)
    async def on_download(self, interaction: discord.Interaction, _: Button):
        await interaction.response.defer()
        path = os.path.join(self.server.instance.home, 'Config', 'serverSettings.lua')
        file = await self.server.node.read_file(path)
        await interaction.followup.send(file=discord.File(fp=BytesIO(file), filename=os.path.basename(path)))

    @discord.ui.button(label='Upload', style=ButtonStyle.primary, emoji='📥', row=4)
    async def on_upload(self, interaction: discord.Interaction, _: Button):
        modal = UploadModal(self.config)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='Dismiss', style=ButtonStyle.red, emoji='🚮', row=4)
    async def on_cancel(self, interaction: discord.Interaction, _: Button):
        await interaction.response.defer()
        self.cancelled = True
        self.channel_update = False
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item, /) -> None:
        await interaction.response.send_message(f"An Error has occured: {error}", ephemeral=True)

import asyncio
import discord
import os
import psycopg

from contextlib import suppress
from core import (Server, Report, Status, ReportEnv, Player, Member, DataObjectFactory, utils, get_translation, Side,
                  ServiceRegistry)
from core.utils.discord import PagedSelectView
from core.utils.presets import (REFUSAL_NO_PICK, REFUSAL_UNKNOWN_FILE, REFUSAL_UNKNOWN_PRESET,
                                PresetResolution, list_preset_files, preset_file_label, preset_names_of,
                                preset_option_value, read_node_presets, resolve_picked_values)
from datetime import datetime, timezone
from discord import SelectOption, ButtonStyle, app_commands
from discord.ui import View, Select, Button, Modal, TextInput
from io import StringIO
from ruamel.yaml import YAML
from services.bot import DCSServerBot, BotService
from typing import Any, NamedTuple, cast

from services.servicebus import ServiceBus

WARNING_ICON = "https://github.com/Special-K-s-Flightsim-Bots/DCSServerBot/blob/master/images/warning.png?raw=true"
_ = get_translation(__name__.split('.')[1])


def ban_targets(member, servers: dict, admin_roles=None) -> list[str]:
    """The servers a ban issued by *member* covers: :func:`core.utils.discord.member_ban_targets`.

    *admin_roles* is the bot's configured Admin role (``<bot>.roles['Admin']``): an issuer holding
    it is unrestricted and the ban is bot-wide.
    """
    return utils.member_ban_targets(member, servers, admin_roles)


def ban_scope_label(targets: list[str]) -> str:
    """The human-readable scope of a ban the ``server_name`` rows of *targets* cover."""
    return _("all servers") if not targets else ", ".join(targets)


class ServerView(View):
    def __init__(self, server: Server):
        super().__init__()
        self.server: Server = server
        self.env: ReportEnv | None = None
        self.modify_mission = True

    async def render(self, interaction: discord.Interaction) -> discord.Embed:
        report = Report(interaction.client, 'mission', 'serverStatus.json')
        self.env = await report.render(server=self.server)
        self.clear_items()
        missions = (await self.server.getMissionList())[:25]
        if len(missions) > 1:
            select: Select = Select(placeholder="Select another mission", options=[
                SelectOption(label=os.path.basename(x)[:-4], value=str(idx))
                for idx, x in enumerate(missions)
                if idx <= 25
            ])
            select.callback = self.load_mission
            self.add_item(select)
#        presets = self.get_presets()
#        if presets:
#            select: Select = Select(placeholder="Select a preset", options=[
#                SelectOption(label=x, value=str(idx))
#                for idx, x in enumerate(self.get_presets())
#                if idx <= 25
#            ])
#            select.callback = self.change_preset
#            self.add_item(select)
        if self.server.status in [Status.PAUSED, Status.STOPPED]:
            button: Button = Button(style=ButtonStyle.primary, emoji='▶️')
            button.callback = self.run
            self.add_item(button)
        elif self.server.status == Status.RUNNING:
            button: Button = Button(style=ButtonStyle.primary, emoji='⏸️')
            button.callback = self.pause
            self.add_item(button)
        if self.server.status in [Status.RUNNING, Status.PAUSED]:
            button: Button = Button(style=ButtonStyle.primary, emoji='⏹️')
            button.callback = self.stop_server
            self.add_item(button)
            button: Button = Button(style=ButtonStyle.primary, emoji='🔁')
            button.callback = self.reload
            self.add_item(button)
        button: Button = Button(style=ButtonStyle.primary if self.modify_mission else ButtonStyle.gray,
                                emoji='⛅' if self.modify_mission else '🚫')
        button.callback = self.toggle_modify
        self.add_item(button)
        button: Button = Button(label='Quit', style=ButtonStyle.red)
        button.callback = self.quit
        self.add_item(button)
        return self.env.embed

    async def load_mission(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.env.embed.set_footer(text="Loading mission, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        if not await self.server.loadMission(int(interaction.data['values'][0]) + 1,
                                             modify_mission=self.modify_mission):
            self.env.embed.set_footer(text="Mission loading failed.", icon_url=WARNING_ICON)
            await interaction.edit_original_response(embed=self.env.embed)
        else:
            with suppress(TimeoutError, asyncio.TimeoutError):
                await self.server.wait_for_status_change([Status.RUNNING], 2)
            await self.render(interaction)
            await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def change_preset(self, interaction: discord.Interaction):
        pass

    async def run(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.server.status == Status.STOPPED:
            self.env.embed.set_footer(text="Starting, please wait ...")
            await interaction.edit_original_response(embed=self.env.embed)
            await self.server.start()
            with suppress(TimeoutError, asyncio.TimeoutError):
                await self.server.wait_for_status_change([Status.RUNNING], 2)
        else:
            await self.server.current_mission.unpause()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.server.current_mission.pause()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def stop_server(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.env.embed.set_footer(text="Stopping server, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        await self.server.stop()
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def reload(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.env.embed.set_footer(text="Restarting, please wait ...")
        await interaction.edit_original_response(embed=self.env.embed)
        await self.server.restart(modify_mission=self.modify_mission)
        # wait for a possible resume
        with suppress(TimeoutError, asyncio.TimeoutError):
            await self.server.wait_for_status_change([Status.RUNNING], 2)
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def toggle_modify(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.modify_mission = not self.modify_mission
        await self.render(interaction)
        await interaction.edit_original_response(embed=self.env.embed, view=self)

    async def quit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()


class PresetView(View):
    def __init__(self, options: list[discord.SelectOption], multi: bool = True):
        super().__init__()
        select: Select = cast(Select, self.children[0])
        select.options = options
        if multi:
            select.max_values = min(10, len(options))
        else:
            select.max_values = 1
        self.result: list[str] | None = None

    @discord.ui.select(placeholder="Select the preset(s) you want to apply")
    async def callback(self, interaction: discord.Interaction, select: Select):
        self.result = select.values
        await interaction.response.defer()

    @discord.ui.button(label='OK', style=ButtonStyle.green)
    async def ok(self, interaction: discord.Interaction, _: Button):
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label='Cancel', style=ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, _: Button):
        await interaction.response.defer()
        self.result = None
        self.stop()


#: The most presets ONE pick may tick. A paged select bounds what a PAGE accepts, so the total is
#: checked by the pick itself (:func:`pick_presets`) before anything is applied.
PRESET_PICK_MAX = 10
#: Options one page of the pick holds — Discord's own limit for a single select.
PRESET_PAGE_SIZE = 25
#: The pick's "every file" filter: no file named, so every file a node carries is offered.
PRESET_FILE_ALL = ""


def preset_pages(files: dict[str, dict[str, Any]], file_label: str = "") -> list[list[SelectOption]]:
    """The pick's options, at most :data:`PRESET_PAGE_SIZE` to a page, in the node's own order.

    One option per name a file OFFERS (:func:`core.utils.presets.preset_names_of`), labelled with the
    preset's NAME and described by the file it comes from, so a pick spanning files says where each
    preset lives. The value carries its file (:func:`core.utils.presets.preset_option_value`), so a
    name two files define applies the body of the file it was ticked in.

    *file_label* narrows the pages to ONE file; :data:`PRESET_FILE_ALL` offers every file. The order
    is the read's own — file label order, then the preset's order in its file — because the caller
    merges the picked documents in the order they are returned.
    """
    pages: list[list[SelectOption]] = []
    for label, document in files.items():
        if file_label and label != file_label:
            continue
        for name in preset_names_of(document):
            if not pages or len(pages[-1]) == PRESET_PAGE_SIZE:
                pages.append([])
            pages[-1].append(SelectOption(label=name, value=preset_option_value(label, name),
                                          description=label))
    return pages


class PresetPickView(PagedSelectView):
    """The cross-file preset pick: pages of names, ticks kept across pages.

    The paged select bounds what ONE page accepts; the total is what the caller checks against
    :data:`PRESET_PICK_MAX` once ``result`` is in.
    """

    def __init__(self, pages: list[list[SelectOption]], placeholder: str | None = None):
        super().__init__(pages=pages, placeholder=placeholder,
                         min_values=1, max_values=min(PRESET_PICK_MAX, PRESET_PAGE_SIZE))


#: What a REFUSED pick tells the operator. The reasons are
#: :mod:`core.utils.presets`'s own; the words belong to this surface.
PRESET_REFUSAL_SENTENCES: dict[str, str] = {
    REFUSAL_NO_PICK: _('No preset was picked, so nothing was applied.'),
    REFUSAL_UNKNOWN_FILE: _('One of the picked presets named a file this node no longer carries.'),
    REFUSAL_UNKNOWN_PRESET: _('One of the picked presets is not in its file any more.'),
}
#: The sentence a pick that resolved nothing is reported with, for a reason this surface cannot name.
PRESET_REFUSAL_UNKNOWN = _('The picked presets could not be applied.')
#: What a pick the operator CANCELLED is answered with.
PRESET_PICK_CANCELLED = _('Cancelled — nothing was applied.')
#: What a pick nobody answered inside the timeout is answered with.
PRESET_PICK_TIMED_OUT = _('The pick timed out, so nothing was applied.')


#: What a pick on a launch that will not run the extensions is refused with: the pick is a REQUEST
#: TO APPLY, and only a launch that modifies the mission applies one.
PRESETS_NEED_EXTENSIONS = _('Not done: a preset was picked, but this launch does not run the extensions, '
                            'so nothing would apply it. Either run the extensions or clear the preset pick.')


class PresetPick(NamedTuple):
    """A pick that RESOLVED: the pick's own values, in list order, and the rule's answer for them.

    ``values`` is what the caller shows (each entry carries the file it was ticked in) and
    ``resolution`` is what it applies — both come from ONE resolution, so a display cannot disagree
    with the documents.
    """

    values: list[str]
    resolution: PresetResolution


def launch_preset_kwargs(answer: PresetResolution) -> dict[str, Any]:
    """The ``preset`` / ``preset_name`` keywords a launch states for a resolved pick.

    EMPTY when the pick resolved nothing, so a launch nobody picked a preset for is exactly the call
    it always was. ``preset`` is the DOCUMENT for a single picked document and a LIST of documents
    when the pick resolves several (a combination contributes one per member), which is the shape
    ``Server.startup`` / ``Server.loadMission`` take. ``preset_name`` is every picked name, in the
    same order, so the node's record names each of them instead of recording a temporary preset.
    """
    documents = list(answer.documents)
    if not documents:
        return {}
    preset: Any = documents[0] if len(documents) == 1 else documents
    kwargs: dict[str, Any] = {'preset': preset}
    names = list(answer.names)
    if names:
        kwargs['preset_name'] = names
    return kwargs


def picked_preset_documents(values: list[str], files: dict[str, dict[str, Any]],
                            file_label: str = "") -> dict[str, Any]:
    """The pick's entries as ``{picked name: document | list of documents}``, in picked order.

    For a surface that SHOWS what was chosen: a combination (a name standing for several documents)
    answers a list, a plain preset its one document. Each entry is resolved through the same rule the
    whole pick is (:func:`core.utils.presets.resolve_picked_values`), so the display cannot disagree
    with what is applied.
    """
    shown: dict[str, Any] = {}
    for value in values:
        one = resolve_picked_values([value], files, named_file=file_label)
        if one.refused or not one.names:
            continue
        documents = list(one.documents)
        shown[one.names[0]] = documents[0] if len(documents) == 1 else documents
    return shown


async def pick_presets(ctx: Any, files: dict[str, dict[str, Any]], *,
                       file_label: str = PRESET_FILE_ALL,
                       ephemeral: bool = False) -> PresetPick | None:
    """Draw the preset pick over *files* and answer its resolution, or ``None`` when nothing starts.

    Every way a pick can end without an answer is reported HERE, in one sentence, so no caller can
    start a launch with a pick it never honoured: a pick the operator cancelled, a pick that timed
    out, a pick with no tick, a pick over :data:`PRESET_PICK_MAX`, and a pick the rule refuses. The
    first three read differently (``Cancelled — nothing was applied.`` / ``The pick timed out, so
    nothing was applied.`` / ``No preset was picked, so nothing was applied.``), because
    ``PagedSelectView.cancelled`` is what separates a cancel from a timeout.

    *ctx* is anything that can ``send`` — a ``commands.Context``, or a double of one. The message
    the pick was drawn on is deleted once the view closes.
    """
    pages = preset_pages(files, file_label)
    if not pages:
        await ctx.send(_('There are no presets to choose from.'), ephemeral=True)
        return None
    view = PresetPickView(pages, placeholder=_('Select the preset(s) you want to apply'))
    msg = await ctx.send(view=view, ephemeral=ephemeral)
    try:
        await view.wait()
    finally:
        with suppress(discord.NotFound):
            await msg.delete()
    picked = view.result
    if view.cancelled:
        await ctx.send(PRESET_PICK_CANCELLED, ephemeral=ephemeral)
        return None
    if picked is None:
        await ctx.send(PRESET_PICK_TIMED_OUT, ephemeral=ephemeral)
        return None
    if not picked:
        await ctx.send(_('No preset was picked, so nothing was applied.'), ephemeral=ephemeral)
        return None
    if len(picked) > PRESET_PICK_MAX:
        await ctx.send(_('You picked {count} presets, but at most {limit} can be applied at once. '
                         'Nothing was applied.').format(count=len(picked), limit=PRESET_PICK_MAX),
                       ephemeral=ephemeral)
        return None
    answer = resolve_picked_values(picked, files, named_file=file_label)
    if answer.refused:
        await ctx.send(PRESET_REFUSAL_SENTENCES.get(answer.reason or "", PRESET_REFUSAL_UNKNOWN),
                       ephemeral=ephemeral)
        return None
    return PresetPick(list(picked), answer)


async def sendable_context(interaction: Any) -> Any:
    """The object a pick or a sentence is sent with: an ``Interaction`` becomes its own context.

    ``get_context`` routes the send to the initial answer before a response and to the followup
    after one, so a command can draw a pick on either side of its defer. Anything that is not an
    ``Interaction`` is taken as the context itself, which is what a double is.
    """
    if isinstance(interaction, discord.Interaction):
        return await interaction.client.get_context(interaction)
    return interaction


async def pick_node_presets(interaction: Any, node: Any, *,
                            file_label: str = PRESET_FILE_ALL,
                            ephemeral: bool = False) -> PresetPick | None:
    """Read *node*'s preset files and draw the pick over them — the ONE entry point the commands use."""
    ctx = await sendable_context(interaction)
    read = await read_node_presets(node)
    return await pick_presets(ctx, read.files, file_label=file_label, ephemeral=ephemeral)


class LaunchPresets(NamedTuple):
    """What a launch states for its weather: the keywords for the launch call, and the pick's raw
    entries (each carrying the file it was ticked in, which a DEFERRED run needs to resolve again)."""

    kwargs: dict[str, Any]
    values: list[str]


async def launch_presets(interaction: Any, node: Any, *, presets: bool | None,
                         presets_file: str | None,
                         ephemeral: bool = False) -> LaunchPresets | None:
    """The preset decision a launch command makes — the ONE place it is made.

    ``LaunchPresets({}, [])`` when no preset was asked for, so the launch is the call it always was.
    ``None`` when a pick was asked for and did NOT resolve (cancelled, timed out, empty, refused,
    or over the limit): the sentence has already been sent and NOTHING may start. Otherwise the
    ``preset`` / ``preset_name`` keywords of the resolved pick and the entries it was made of.

    A ``presets_file`` given without ``presets`` is "yes, pick from that file", the one forgiving
    reading of the two inputs; two inputs that CONTRADICT each other (a pick beside a launch that
    does not run the extensions) are refused by the caller before this runs.
    """
    if not (presets or presets_file):
        return LaunchPresets({}, [])
    picked = await pick_node_presets(interaction, node, file_label=presets_file or "",
                                     ephemeral=ephemeral)
    if picked is None:
        return None
    return LaunchPresets(launch_preset_kwargs(picked.resolution), list(picked.values))


async def preset_file_autocomplete(interaction: Any, current: str) -> list[app_commands.Choice[str]]:
    """The preset-file FILTER: every file the TARGET node carries, as its own ``config/…`` label.

    Both shapes a preset file has (``presets*.yaml`` and ``presets/*.yaml``) are listed through the
    core discovery, so the labels offered are exactly the labels a pick resolves against. Omitted,
    the filter means every file (:data:`PRESET_FILE_ALL`).
    """
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server = await utils.ServerTransformer().transform(interaction, interaction.namespace.server)
        node = getattr(server, 'node', None) or interaction.client.node
        listed, _listed_ok = await list_preset_files(node)
        config_dir = str(getattr(node, 'config_dir', '') or '')
        labels = [preset_file_label(config_dir, path) for path, _version in listed]
        return [app_commands.Choice[str](name=label, value=label)
                for label in labels
                if not current or current.casefold() in label.casefold()][:PRESET_PAGE_SIZE]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class InfoView(View):

    def __init__(self, member: discord.Member | str, bot: DCSServerBot, ephemeral: bool,
                 player: Player | None = None, server: Server | None = None):
        super().__init__()
        self.member = member
        self.bot = bot
        self.bus = bot.bus
        self.ephemeral = ephemeral
        self.player = player
        self.server = server
        self.ucid = None
        if not isinstance(self.member, discord.Member):
            self.ucid = self.member

    async def render(self) -> discord.Embed:
        if isinstance(self.member, discord.Member):
            self._member = cast(Member, await DataObjectFactory().new(
                Member, name=self.member.name, node=self.bot.node, member=self.member
            ).prep())
            self.ucid = self._member.ucid
        else:
            self._member = None
        if not self._member or self._member.ucid:
            if isinstance(self.member, discord.Member):
                button = Button(emoji="🔀")
                button.callback = self.on_unlink
                self.add_item(button)
                if not self._member.verified:
                    button = Button(emoji="💯")
                    button.callback = self.on_verify
                    self.add_item(button)
            banned = await self.is_banned()
            if banned:
                button = Button(emoji="✅")
                button.callback = self.on_unban
                self.add_item(button)
            else:
                button = Button(emoji="⛔")
                button.callback = self.on_ban
                self.add_item(button)
            watchlist = await self.is_watchlist()
            if watchlist:
                button = Button(emoji="🆓")
                button.callback = self.on_unwatch
                self.add_item(button)
            else:
                button = Button(emoji="🔍")
                button.callback = self.on_watch
                self.add_item(button)
            if self.player:
                button = Button(emoji="⏏️")
                button.callback = self.on_kick
                self.add_item(button)
        else:
            banned = watchlist = False
        button = Button(label="Cancel", style=ButtonStyle.red)
        button.callback = self.on_cancel
        self.add_item(button)
        report = Report(self.bot, 'mission', 'info.json')
        env = await report.render(member=self.member, ucid=self.ucid, player=self.player, banned=banned,
                                  watchlist=watchlist)
        return env.embed

    async def is_banned(self) -> bool:
        # no server context (the player is not on any server) asks the unscoped question
        server_name = self.server.name if self.server else None
        return await self.bus.is_banned(self.ucid, server_name) is not None

    async def is_watchlist(self) -> bool:
        async with self.bot.apool.connection() as conn:
            cursor = await conn.execute("SELECT True FROM watchlist WHERE player_ucid = %s", (self.ucid,))
            row = await cursor.fetchone()
        return row[0] if row else False

    async def on_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()

    async def on_ban(self, interaction: discord.Interaction):
        modal = BanModal(self.ucid)
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.stop()

    async def on_unban(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # the same issuer scope the ban modal uses: a narrowed issuer clears their own servers' rows
        # only, an unscoped one (no target) clears every row of that ucid
        targets = ban_targets(interaction.user, self.bus.servers, self.bot.roles['Admin'])
        cleared = 0
        for target in targets or [None]:
            cleared += await self.bus.unban(self.ucid, target)
        if cleared:
            message = _("User {} unbanned on {}.").format(self.ucid, ban_scope_label(targets))
        else:
            message = _("User {} was not banned on {}.").format(self.ucid, ban_scope_label(targets))
        await interaction.followup.send(message, ephemeral=self.ephemeral)
        await self.bot.audit(f'unbanned user {self.ucid}.', user=interaction.user)
        self.stop()

    async def on_kick(self, interaction: discord.Interaction):
        await interaction.response.defer()
        # TODO: reason modal
        await self.server.kick(player=self.player)
        await interaction.followup.send("User has been kicked.", ephemeral=self.ephemeral)
        await self.bot.audit(f'kicked player {self.player.name} (ucid={self.player.ucid}).', user=interaction.user)
        self.stop()

    async def on_unlink(self, interaction: discord.Interaction):
        await interaction.response.defer()
        member: discord.Member = self._member.member
        self._member.unlink()
        if self.player:
            await self.player.prep()
        # propagate the event
        await self.bus.send_to_node({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "propagate_event",
            "params": {
                "command": "onMemberUnlinked",
                "server": self.server.name,
                "data": {
                    "ucid": self.ucid,
                    "discord_id": member.id
                }
            }
        })
        await interaction.followup.send("Member has been unlinked.", ephemeral=self.ephemeral)
        # If autorole is enabled, remove the DCS role from the user:
        autorole = self.bot.locals.get('autorole', {}).get('linked')
        if autorole:
            try:
                await member.remove_roles(self.bot.get_role(autorole))
            except discord.Forbidden:
                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
        await self.bot.audit(f'unlinked member {member.display_name} from UCID {self.ucid}.', user=interaction.user)
        self.stop()

    async def on_verify(self, interaction: discord.Interaction):
        await interaction.response.defer()
        member: discord.Member = self._member.member
        self._member.verified = True
        if self.player:
            await self.player.prep()
        await self.bus.send_to_node({
            "command": "rpc",
            "service": "ServiceBus",
            "method": "propagate_event",
            "params": {
                "command": "onMemberLinked",
                "server": self.server.name,
                "data": {
                    "ucid": self.ucid,
                    "discord_id": member.id
                }
            }
        })
        await interaction.followup.send("Member has been verified.", ephemeral=self.ephemeral)
        # If autorole is enabled, give the user the role:
        autorole = self.bot.locals.get('autorole', {}).get('linked')
        if autorole:
            try:
                await member.add_roles(self.bot.get_role(autorole))
            except discord.Forbidden:
                await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)
        await self.bot.audit(f'linked member {member.display_name} to UCID {self.ucid}.', user=interaction.user)
        self.stop()

    async def on_watch(self, interaction: discord.Interaction):
        modal = WatchModal(self.ucid)
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.stop()

    async def on_unwatch(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with self.bot.apool.connection() as conn:
            await conn.execute("DELETE FROM watchlist WHERE player_ucid = %s", (self.ucid, ))
        await interaction.followup.send("User removed from the watchlist.", ephemeral=self.ephemeral)
        name = self.player.name if self.player else self.member.display_name if isinstance(self.member, discord.Member) else self.member
        message = f'removed player {name} '
        if not utils.is_ucid(name):
            message += '(ucid={self.ucid}) '
        message += 'from the watchlist'
        await self.bot.audit(message, user=interaction.user)
        self.stop()


class ModifyView(View):
    def __init__(self, presets: dict, mission_change: str, warehouses_change: str, options_change: str):
        super().__init__()
        self.presets = presets
        self.embed = discord.Embed(color=discord.Color.blue())
        self.render()
        self.mission_change = self.cut(mission_change)
        self.warehouses_change = self.cut(warehouses_change)
        self.options_change = self.cut(options_change)

        button = Button(label="Presets", style=ButtonStyle.primary)
        button.callback = self.display_presets
        self.add_item(button)

        if self.mission_change:
            button = Button(label="mission", style=ButtonStyle.secondary)
            button.callback = self.display_mission
            self.add_item(button)

        if self.warehouses_change:
            button = Button(label="warehouses", style=ButtonStyle.secondary)
            button.callback = self.display_warehouses
            self.add_item(button)

        if self.options_change:
            button = Button(label="options", style=ButtonStyle.secondary)
            button.callback = self.display_options
            self.add_item(button)

        button = Button(label="Cancel", style=ButtonStyle.red)
        button.callback = self.cancel
        self.add_item(button)

    @staticmethod
    def cut(message: str | None = None) -> str:
        if not message or len(message) <= 4096:
            return message
        remark = f"``` ... {len(message) - 4096} more"
        return message[:4096 - len(remark)] + remark

    def render(self):
        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.default_flow_style = False
        yaml.sort_keys = True
        stream = StringIO()

        self.embed.title = 'Presets'
        self.embed.description = 'These modifications will be applied to your mission:\n\n'
        for k, v in self.presets.items():
            yaml.dump(v, stream)
            self.embed.description += "{k}:\n```yaml\n{v}\n```".format(k=k, v=stream.getvalue())

    async def display_presets(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.render()
        await interaction.edit_original_response(embed=self.embed)

    async def display_mission(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.embed.title = 'mission'
        self.embed.description = self.mission_change
        await interaction.edit_original_response(embed=self.embed)

    async def display_warehouses(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.embed.title = 'warehouses'
        self.embed.description = self.warehouses_change
        await interaction.edit_original_response(embed=self.embed)

    async def display_options(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.embed.title = 'options'
        self.embed.description = self.options_change
        await interaction.edit_original_response(embed=self.embed)

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()


class AirbaseView(View):
    def __init__(self, server: Server, airbase: dict, data: dict):
        super().__init__(timeout=None)
        self.server = server
        self.airbase = airbase
        self.data = data
        self.render()

    def render(self):
        capture = cast(discord.ui.Button, self.children[0])
        capture.style = discord.ButtonStyle.blurple if self.data['coalition'] == 1 else discord.ButtonStyle.red
        capture_toggle = cast(discord.ui.Button, self.children[1])
        capture_toggle.style = discord.ButtonStyle.primary if not self.data['auto_capture'] else discord.ButtonStyle.secondary
        capture_toggle.label = _('Auto Capture') if not self.data['auto_capture'] else _('No Auto Capture')
        radio_toggle = cast(discord.ui.Button, self.children[2])
        radio_toggle.style = discord.ButtonStyle.primary if not self.data['radio_silent'] else discord.ButtonStyle.secondary
        radio_toggle.label = _('ATC On') if self.data['radio_silent'] else _('ATC Off')
        # noinspection PyUnresolvedReferences
        self.children[3].disabled = all(x is True for x in self.data['unlimited'].values())

    @discord.ui.button(label=_('Capture'))
    async def capture(self, interaction: discord.Interaction, _button: Button):
        await interaction.response.defer()
        self.data['coalition'] = (self.data['coalition'] % 2) + 1
        await self.server.send_to_dcs_sync({
            "command": "captureAirbase",
            "name": self.airbase['name'],
            "coalition": self.data['coalition']
        })
        self.data['auto_capture'] = False
        self.airbase['coalition'] = data['coalition']
        self.render()
        msg = await interaction.original_response()
        embed = msg.embeds[0]
        colors = {
            1: discord.Color.red(),
            2: discord.Color.blue()
        }
        embed.colour = colors[self.data['coalition']]
        embed.set_field_at(1, name=_('Coalition'), value=Side(self.data['coalition']).name.title())
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button()
    async def toggle_capture(self, interaction: discord.Interaction, _button: Button):
        await interaction.response.defer()
        self.data['auto_capture'] = not self.data['auto_capture']
        await self.server.send_to_dcs({
            "command": "setAutoCapture",
            "name": self.airbase['name'],
            "value": self.data['auto_capture']
        })
        self.render()
        await interaction.edit_original_response(view=self)

    @discord.ui.button()
    async def silence_radio(self, interaction: discord.Interaction, _button: Button):
        await interaction.response.defer()
        self.data['radio_silent'] = not self.data['radio_silent']
        await self.server.send_to_dcs({
            "command": "setRadioSilentMode",
            "name": self.airbase['name'],
            "value": self.data['radio_silent']
        })
        self.render()
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label=_("Download Inventory"), style=discord.ButtonStyle.green)
    async def download(self, interaction: discord.Interaction, _button: Button):
        from .commands import Mission

        await interaction.response.defer()
        await Mission.download_warehouse(interaction, self.airbase, self.data)
        self.stop()

    @discord.ui.button(label=_('Cancel'), style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: Button):
        await interaction.response.defer()
        self.stop()


class BanModal(Modal):
    reason = TextInput(label=_("Reason"), max_length=80, required=True)
    period = TextInput(label=_("Days (empty = forever)"), required=False)

    def __init__(self, ucid: str):
        super().__init__(title=_("Ban Details"))
        self.ucid = ucid
        self.bus: ServiceBus = cast(ServiceBus, ServiceRegistry.get(ServiceBus))
        self.bot: BotService = cast(BotService, ServiceRegistry.get(BotService))

    async def on_submit(self, interaction: discord.Interaction):
        days = int(self.period.value) if self.period.value else None
        targets = ban_targets(interaction.user, self.bus.servers, self.bot.bot.roles['Admin'])
        stamp = datetime.now(tz=timezone.utc)
        for target in targets or [None]:
            await self.bus.ban(self.ucid, interaction.user.display_name, self.reason.value, days,
                               server_name=target, banned_at=stamp)
        name = await self.bot.bot.get_member_or_name_by_ucid(self.ucid)
        if isinstance(name, discord.Member):
            name = name.display_name
        elif not name:
            name = '<unknown>'
        await interaction.response.send_message(
            _("User {} banned on {} ").format(name, ban_scope_label(targets)) +
            (_("for {} days.").format(days) if days else ""),
            ephemeral=utils.get_ephemeral(interaction))
        await self.bot.audit(f'banned player {name} (ucid={self.ucid} with reason "{self.reason.value}"' +
                             (f' for {days} days.' if days else ' permanently.'), user=interaction.user)


class WatchModal(Modal):
    reason = TextInput(label=_("Reason"), max_length=80, required=True)

    def __init__(self, ucid: str):
        super().__init__(title=_("Watch Details"))
        self.ucid = ucid
        self.bot: BotService = cast(BotService, ServiceRegistry.get(BotService))

    async def on_submit(self, interaction: discord.Interaction):
        name = await self.bot.bot.get_member_or_name_by_ucid(self.ucid)
        if isinstance(name, discord.Member):
            name = name.display_name
        elif not name:
            name = '<unknown>'
        try:
            async with self.bot.apool.connection() as conn:
                await conn.execute("INSERT INTO watchlist (player_ucid, reason, created_by) VALUES (%s, %s, %s)",
                                   (self.ucid, self.reason.value, interaction.user.display_name))
            await interaction.response.send_message(_("Player {} is now on the watchlist.").format(name),
                ephemeral=utils.get_ephemeral(interaction))
            await self.bot.audit(
                f'added player {name} (ucid={self.ucid} with reason "{self.reason.value}" to the watchlist',
                user=interaction.user
            )
        except psycopg.errors.UniqueViolation:
            await interaction.response.send_message(
                _("Player {} was already on the watchlist.").format(name),
                ephemeral=utils.get_ephemeral(interaction)
            )

import asyncio
from queue import Queue, Empty

import aiohttp
import certifi
import discord
import ssl

from core import Plugin, utils, Status, Group, DEFAULT_TAG, get_translation, Coalition
from discord import app_commands
from discord.ext import tasks

_ = get_translation(__name__.split('.')[1])


class Charity(Plugin):
    def __init__(self, bot):
        super().__init__(bot)
        self._session = None
        self.donations = Queue()

    async def cog_load(self) -> None:
        await super().cog_load()
        configs = self.locals.get(DEFAULT_TAG, {}).get('gofundme', [])
        interval = 5
        for config in configs:
            if 'interval' in config:
                interval = min(interval, config['interval'])
        if interval != 5:
            self.check_donations.change_interval(minutes=interval)
        self.check_donations.start()
        if self.get_config().get('bot_status', True):
            self.change_presence.start()

    async def cog_unload(self) -> None:
        if self.get_config().get('bot_status', True):
            self.change_presence.cancel()
        self.check_donations.cancel()
        if self._session:
            await self._session.close()
        await super().cog_unload()

    @property
    def session(self):
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where())),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
                raise_for_status=True
            )
        return self._session

    @tasks.loop(minutes=5)
    async def check_donations(self):
        configs = self.locals.get(DEFAULT_TAG, {}).get('gofundme', [])
        for config in configs:
            try:
                await self._check_gofundme(config)
            except Exception as ex:
                self.log.exception(ex)

    @check_donations.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=5)
    async def change_presence(self):
        try:
            donation = self.donations.get_nowait()
            message = f"{donation['name']} donated {donation['amount']}"
            activity = discord.CustomActivity(name=message)
            await self.bot.change_presence(status=discord.Status.online, activity=activity)
            self.donations.task_done()
        except Empty:
            pass

    @change_presence.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def _check_gofundme(self, config: dict):
        campaign = config['campaign']
        slug = campaign.split('/')[-1]
        api_url = f"https://gateway.gofundme.com/web-gateway/v1/feed/{slug}/donations?limit=20&offset=0"

        async with self.session.get(api_url) as response:
            data = await response.json()
            donations = data.get('references', {}).get('donations', [])
            if not donations:
                return

            # Check if we have any donations recorded for this campaign
            has_donations = await self._has_donations(slug)

            for donation in donations:
                d_id = str(donation['donation_id'])
                if await self._is_new_donation(slug, d_id):
                    await self._record_donation(slug, donation)
                    # Only report if we already had some donations (avoid spam on first run)
                    if has_donations:
                        if self.get_config().get('bot_status', True):
                            self.donations.put_nowait(donation)
                        await self._report_donation(config, donation)

            if self.get_config().get('bot_status', True):
                if self.donations.empty():
                    status = await self._get_status(slug)
                    message = _("Raised {total}{currency} from {goal}{currency}").format(
                        title=status['title'], total=status['total'], currency=status['currency'], goal=status['goal'])
                    activity = discord.CustomActivity(name=message, emoji='💵')
                    await self.bot.change_presence(status=discord.Status.online, activity=activity)

    async def _has_donations(self, slug: str) -> bool:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM charity_donations WHERE campaign_id = %s", (slug,))
            return (await cursor.fetchone())[0] > 0

    async def _is_new_donation(self, slug: str, donation_id: str) -> bool:
        async with self.apool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM charity_donations WHERE campaign_id = %s AND donation_id = %s",
                (slug, donation_id)
            )
            return (await cursor.fetchone()) is None

    async def _record_donation(self, slug: str, donation: dict):
        async with self.apool.connection() as conn:
            await conn.execute(
                """INSERT INTO charity_donations (campaign_id, donation_id, amount, name, message, created_at) 
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (campaign_id, donation_id) DO NOTHING""",
                (slug, str(donation['donation_id']), donation['amount'], donation['name'],
                 donation.get('comment') or donation.get('message'), donation['created_at'])
            )

    async def _report_donation(self, config: dict, donation: dict):
        # Discord notification
        channel_id = config.get('channel')
        channel = None
        if isinstance(channel_id, int) or (isinstance(channel_id, str) and channel_id.isdigit()):
            channel = self.bot.get_channel(int(channel_id))
        else:
            channel = discord.utils.get(self.bot.get_all_channels(), name=channel_id)

        if channel:
            campaign_url = config['campaign']
            if not campaign_url.startswith('http'):
                campaign_url = f"https://www.gofundme.com/f/{campaign_url}"
            embed = discord.Embed(title=_("New Donation!"), color=discord.Color.green(), url=campaign_url)
            embed.add_field(name=_("Donor"), value=donation['name'] or _("Anonymous"))
            embed.add_field(name=_("Amount"), value=f"${donation['amount']}")
            if donation.get('comment') or donation.get('message'):
                embed.add_field(name=_("Message"), value=donation.get('comment') or donation.get('message'),
                                inline=False)
            await channel.send(embed=embed)

        # In-game notification
        slug = config['campaign'].split('/')[-1]
        msg = _("New donation: {name} donated ${amount}!").format(
            name=donation['name'] or _('Anonymous'), amount=donation['amount'])
        for server in self.bot.servers.values():
            if server.status in [Status.RUNNING, Status.PAUSED]:
                server_config = self.get_config(server)
                if slug in server_config.get('campaigns', []):
                    await server.sendChatMessage(Coalition.ALL, msg)
                    await server.sendPopupMessage(Coalition.ALL, msg)

    charity = Group(name="charity", description="Commands for charity tracking")

    async def _get_status(self, slug: str) -> dict:
        api_url = f"https://gateway.gofundme.com/web-gateway/v1/feed/{slug}/campaign"
        async with self.session.get(api_url) as response:
            data = await response.json()
            campaign = data.get('references', {}).get('campaign', {})
            if not campaign:
                raise ValueError("Campaign not found.")

        currency = {
            "USD": '$',
            "EUR": '€',
            "GBP": '£'
        }
        return {
            "title": campaign.get('fund_name', slug),
            "total": campaign.get('current_amount', 0),
            "goal": campaign.get('goal_amount', 0),
            "currency": currency.get(campaign.get('currencycode', 'USD'), '$')
        }

    @charity.command(description="Show charity status")
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def status(self, interaction: discord.Interaction):
        configs = self.locals.get(DEFAULT_TAG, {}).get('gofundme', [])
        if not configs:
            await interaction.response.send_message(_("No charity campaigns configured."), ephemeral=True)
            return

        await interaction.response.defer()
        embed = discord.Embed(title=_("Charity Status"), color=discord.Color.blue())
        for config in configs:
            campaign = config['campaign']
            slug = campaign.split('/')[-1]
            try:
                status = await self._get_status(slug)
                value = _("Total: {total}{currency}").format(total=status['total'], currency=status['currency'])
                goal = status['goal']
                if goal:
                    value += _(" / Goal: {goal}{currency} ({percent:.1f}%)").format(
                        goal=goal, currency=status['currency'], percent=(status['total'] / goal * 100) if goal else 0)

                embed.add_field(name=status['title'], value=value, inline=False)
            except Exception as ex:
                self.log.error(f"Error fetching status for {slug}: {ex}")
                embed.add_field(name=slug, value=_("Error fetching status."), inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Charity(bot))

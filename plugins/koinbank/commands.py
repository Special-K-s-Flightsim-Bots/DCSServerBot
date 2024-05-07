import discord

from core import Plugin, DataObjectFactory, Group, utils
from discord import app_commands
from services import DCSServerBot
from .bank import Bank

from .localbank import LocalBank


class KoinBank(Plugin):
    async def cog_load(self) -> None:
        await super().cog_load()
        self.bank = DataObjectFactory().new(Bank, node=self.bus.node, initial_balance=100.0)

    bank = Group(name="bank", description="Commands to manage your bank account")

    @bank.command(description="Display your current bank balance")
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def balance(self, interaction: discord.Interaction):
        ucid = await self.bot.get_ucid_by_member(interaction.user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Use {} to link your account.".format(
                utils.display_command(interaction, name='linkme')), ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            f"Your current balance is: {await self.bank.check_balance(ucid):.2f} Koins.", ephemeral=True)

    @bank.command(description="Display the banks balance")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def total(self, interaction: discord.Interaction):
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            f"You have currently {await self.bank.check_balance(str(interaction.guild_id)):.2f} Koins in your bank.",
            ephemeral=True)


async def setup(bot: DCSServerBot):
    await bot.add_cog(KoinBank(bot))

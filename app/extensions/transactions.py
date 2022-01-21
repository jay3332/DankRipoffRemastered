from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING, Any

import discord

from app.core import Cog, Context, command, simple_cooldown, BAD_ARGUMENT
from app.util.converters import CaseInsensitiveMemberConverter
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


class Transactions(Cog):
    """Commands that handle transactions between the bank or other users."""

    # noinspection PyTypeChecker
    @command(aliases={"w", "with", "wd"})
    @simple_cooldown(1, 8)
    async def withdraw(self, ctx: Context, amount: int) -> Any:  # TODO: amount converter    
        if amount <= 0:
            return "Please provide an amount greater than 0.", BAD_ARGUMENT

        data = await ctx.db.get_user_record(ctx.author.id)

        if amount > data.bank:
            return "You do not have that many coins in your bank.", BAD_ARGUMENT

        async with ctx.typing():
            await data.add(wallet=amount, bank=-amount)

        embed = discord.Embed(color=Colors.primary)
        embed.set_author(name=f"Successful Transaction: {ctx.author}", icon_url=ctx.author.avatar)

        embed.description = f"Withdrew {Emojis.coin} **{amount:,}** from your bank."
        embed.add_field(name="Updated Balance", value=dedent(f"""
            Wallet: {Emojis.coin} **{data.wallet:,}**
            Bank: {Emojis.coin} **{data.bank:,}**
        """))

        return embed

    @command(aliases={"d", "dep"})
    @simple_cooldown(1, 8)
    async def deposit(self, ctx: Context, amount: int) -> Any:  # TODO ^
        if amount <= 0:
            return "Please provide an amount greater than 0."

        data = await ctx.db.get_user_record(ctx.author.id)

        if amount > data.wallet:
            return "You do not have that many coins to deposit.", BAD_ARGUMENT

        space_left = data.max_bank - data.bank
        if amount > space_left:
            return "Your bank cannot hold that many coins yet.", BAD_ARGUMENT

        async with ctx.typing():
            await data.add(wallet=-amount, bank=amount)

        embed = discord.Embed(color=Colors.primary)
        embed.set_author(name=f"Successful Transaction: {ctx.author}", icon_url=ctx.author.avatar)

        embed.description = f"Deposited {Emojis.coin} **{amount:,}** into your bank."
        embed.add_field(name="Updated Balance", value=dedent(f"""
            Wallet: {Emojis.coin} **{data.wallet:,}**
            Bank: {Emojis.coin} **{data.bank:,}**
        """))

        return embed


def setup(bot: Bot) -> None:
    bot.add_cog(Transactions(bot))

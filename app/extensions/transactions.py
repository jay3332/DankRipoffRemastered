from __future__ import annotations

from textwrap import dedent
from typing import Any, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core import Cog, Context, REPLY, command, simple_cooldown
from app.data.items import Item, Items
from app.util.common import query_collection, walk_collection, image_url_from_emoji
from app.util.converters import BankTransaction, DEPOSIT, WITHDRAW
from app.util.pagination import Paginator, FieldBasedFormatter
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


def query_item(query: str, /) -> Item:
    if match := query_collection(Items, Item, query):
        return match

    raise commands.BadArgument(f"I couldn't find a item named {query!r}.")


class Transactions(Cog):
    """Commands that handle transactions between the bank or other users."""

    # noinspection PyTypeChecker
    @command(aliases={"w", "with", "wd"})
    @simple_cooldown(1, 8)
    async def withdraw(self, ctx: Context, *, amount: BankTransaction(WITHDRAW)) -> Any:
        data = await ctx.db.get_user_record(ctx.author.id)

        async with ctx.typing():
            await data.add(wallet=amount, bank=-amount)

        embed = discord.Embed(color=Colors.primary)
        embed.set_author(name=f"Successful Transaction: {ctx.author}", icon_url=ctx.author.avatar)

        embed.description = f"Withdrew {Emojis.coin} **{amount:,}** from your bank."
        embed.add_field(name="Updated Balance", value=dedent(f"""
            Wallet: {Emojis.coin} **{data.wallet:,}**
            Bank: {Emojis.coin} **{data.bank:,}**
        """))

        return embed, REPLY

    # noinspection PyTypeChecker
    @command(aliases={"d", "dep"})
    @simple_cooldown(1, 8)
    async def deposit(self, ctx: Context, *, amount: BankTransaction(DEPOSIT)) -> Any:
        data = await ctx.db.get_user_record(ctx.author.id)

        async with ctx.typing():
            await data.add(wallet=-amount, bank=amount)

        embed = discord.Embed(color=Colors.primary)
        embed.set_author(name=f"Successful Transaction: {ctx.author}", icon_url=ctx.author.avatar)

        embed.description = f"Deposited {Emojis.coin} **{amount:,}** into your bank."
        embed.add_field(name="Updated Balance", value=dedent(f"""
            Wallet: {Emojis.coin} **{data.wallet:,}**
            Bank: {Emojis.coin} **{data.bank:,}**
        """))

        return embed, REPLY

    @command(aliases={"store", "market", "sh", "iteminfo", "ii"})
    @simple_cooldown(1, 6)
    async def shop(self, ctx: Context, *, item: query_item = None) -> Paginator | discord.Embed:
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        record = await ctx.db.get_user_record(ctx.author.id)

        inventory = record.inventory_manager
        await inventory.wait()

        if not item:
            fields = []

            for i in walk_collection(Items, Item):
                if not i.buyable:
                    continue

                embed.title = 'Item Shop'
                embed.description = f'To buy an item, see `{ctx.clean_prefix}buy`.\nTo view information on an item, see `{ctx.clean_prefix}iteminfo`.'

                comment = '*You cannot afford this item.*\n' if i.price > record.wallet else ''
                owned = inventory.cached.quantity_of(i)
                owned = f'(You own {owned:,})' if owned else ''

                fields.append({
                    'name': f'{i.display_name} — {Emojis.coin} {i.price:,} {owned}',
                    'value': comment + i.description,
                    'inline': False,
                })

            return Paginator(ctx, FieldBasedFormatter(embed, fields, per_page=5))

        item: Item

        owned = inventory.cached.quantity_of(item)

        embed.title = f'{item.display_name} ({owned} owned)'
        embed.description = item.description

        embed.set_thumbnail(url=image_url_from_emoji(item.emoji))

        embed.add_field(name='General', value=dedent(f"""
            Name: {item.get_display_name(bold=True)}
            Query Key: `{item.key}`
            Type: **{item.type.name.title()}**
        """))

        embed.add_field(name='Pricing', value=dedent(f"""
            Buy Price: {Emojis.coin} **{item.price:,}**
            {
                f'Sell Value: {Emojis.coin} **{item.sell:,}**' if item.sellable else ''
            }
        """))

        embed.add_field(name='Flexibility', value=dedent(f"""
            Buyable? **{self._bool_to_human(item.buyable)}**
            Sellable? **{self._bool_to_human(item.sellable)}**
            Usable? **{self._bool_to_human(item.usable)}**
            Removable? **{self._bool_to_human(item.removable)}**
        """), inline=False)

        return embed

    @staticmethod
    def _bool_to_human(b: bool) -> str:
        return 'Yes' if b else 'No'


def setup(bot: Bot) -> None:
    bot.add_cog(Transactions(bot))

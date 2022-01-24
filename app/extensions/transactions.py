from __future__ import annotations

from textwrap import dedent
from typing import Any, TYPE_CHECKING

import discord

from app.core import Cog, Context, REPLY, command, simple_cooldown, user_max_concurrency
from app.data.items import Item, Items
from app.util.common import image_url_from_emoji, walk_collection
from app.util.converters import BUY, BankTransaction, DEPOSIT, ItemAndQuantityConverter, SELL, USE, WITHDRAW, query_item
from app.util.pagination import FieldBasedFormatter, Paginator
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


class Transactions(Cog):
    """Commands that handle transactions between the bank or other users."""

    # noinspection PyTypeChecker
    @command(aliases={"w", "with", "wd"})
    @simple_cooldown(1, 8)
    async def withdraw(self, ctx: Context, *, amount: BankTransaction(WITHDRAW)) -> Any:
        """Withdraw coins from your bank."""
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
        """Deposit coins from your wallet into your bank."""
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
    async def shop(self, ctx: Context, *, item: query_item = None) -> tuple[Paginator | discord.Embed, Any]:
        """View the item shop, or view information on a specific item."""
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
                    'name': f'• {i.display_name} — {Emojis.coin} {i.price:,} {owned}',
                    'value': comment + i.description,
                    'inline': False,
                })

            return Paginator(ctx, FieldBasedFormatter(embed, fields, per_page=5)), REPLY

        item: Item

        owned = inventory.cached.quantity_of(item)

        embed.title = f'{item.display_name} ({owned} owned)'
        embed.description = item.description

        embed.set_thumbnail(url=image_url_from_emoji(item.emoji))

        embed.add_field(name='General', value=dedent(f"""
            Name: {item.get_display_name(bold=True)}
            Query Key: **`{item.key}`**
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

        return embed, REPLY

    @staticmethod
    def _bool_to_human(b: bool) -> str:
        return 'Yes' if b else 'No'

    @command(alias='purchase')
    @simple_cooldown(3, 8)
    async def buy(self, ctx: Context, *, item_and_quantity: ItemAndQuantityConverter(BUY)) -> tuple[discord.Embed | str, Any]:
        """Buy items!"""
        item, quantity = item_and_quantity
        price = item.price * quantity

        if not await ctx.confirm(
            f'Are you sure you want to buy {item.get_sentence_chunk(quantity)} for {Emojis.coin} **{price:,}**?',
            delete_after=True,
            reference=ctx.message,
        ):
            return 'Cancelled purchase.', REPLY

        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = record.inventory_manager

        async with ctx.db.acquire() as conn:
            await record.add_random_exp(10, 15, chance=0.5, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.5, connection=conn)

            await record.add(wallet=-price, connection=conn)
            await inventory.add_item(item, quantity, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)
        embed.description = f'You bought {item.get_sentence_chunk(quantity)} for {Emojis.coin} **{price:,}** coins.'
        embed.set_author(name=f'Successful Purchase: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_thumbnail(url=image_url_from_emoji(item.emoji))

        return embed, REPLY

    @command(alias='s')
    @simple_cooldown(3, 8)
    async def sell(self, ctx: Context, *, item_and_quantity: ItemAndQuantityConverter(SELL)) -> tuple[discord.Embed | str, Any]:
        """Sell items from your inventory for coins."""
        item, quantity = item_and_quantity
        value = item.sell * quantity

        if not await ctx.confirm(
            f'Are you sure you want to sell {item.get_sentence_chunk(quantity)} in exchange for {Emojis.coin} **{value:,}**?',
            delete_after=True,
            reference=ctx.message,
        ):
            return 'Cancelled transaction.', REPLY

        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = record.inventory_manager

        async with ctx.db.acquire() as conn:
            await record.add_random_exp(10, 15, chance=0.4, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.4, connection=conn)

            await record.add(wallet=value, connection=conn)
            await inventory.add_item(item, -quantity, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)
        embed.description = f'You sold {item.get_sentence_chunk(quantity)} in exchange for {Emojis.coin} **{value:,}** coins.'
        embed.set_author(name=f'Successful Transaction: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_thumbnail(url=image_url_from_emoji(item.emoji))

        return embed, REPLY

    @command(aliases={'u', 'consume'})
    @simple_cooldown(2, 10)
    @user_max_concurrency(1)
    async def use(self, ctx: Context, *, item_and_quantity: ItemAndQuantityConverter(USE)):
        """Use the items you own!"""
        item, quantity = item_and_quantity

        record = await ctx.db.get_user_record(ctx.author.id)

        async with ctx.db.acquire() as conn:
            await record.add_random_exp(10, 15, chance=0.4, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.4, connection=conn)

            quantity = await item.use(ctx, quantity)

            if item.dispose:
                await record.inventory_manager.add_item(item, -quantity, connection=conn)

        await ctx.thumbs()


def setup(bot: Bot) -> None:
    bot.add_cog(Transactions(bot))

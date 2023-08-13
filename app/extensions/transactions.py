from __future__ import annotations

import asyncio
import re
from functools import partial
from textwrap import dedent
from typing import Any, Callable, Literal, TYPE_CHECKING, TypeAlias

import discord
from discord import app_commands
from discord.ext import commands

from app.core import (
    BAD_ARGUMENT,
    Cog,
    Command,
    Context,
    EDIT,
    HybridCommand,
    HybridContext, NO_EXTRA,
    REPLY,
    command,
    lock_transactions,
    simple_cooldown,
    user_max_concurrency,
)
from app.data.items import Item, ItemRarity, ItemType, Items
from app.data.pets import Pets
from app.data.recipes import Recipe, Recipes
from app.util.common import (
    cutoff,
    get_by_key,
    image_url_from_emoji,
    progress_bar,
    query_collection,
    query_collection_many,
    walk_collection,
)
from app.util.converters import (
    BUY,
    BankTransaction,
    CaseInsensitiveMemberConverter,
    DEPOSIT,
    DROP,
    DropAmount,
    ItemAndQuantityConverter,
    RecipeConverter,
    SELL,
    USE,
    WITHDRAW,
    get_amount,
    query_item,
    query_recipe,
    transform_item_and_quantity,
)
from app.util.pagination import FieldBasedFormatter, Paginator
from app.util.structures import LockWithReason
from app.util.views import CommandInvocableModal, ConfirmationView, ModalButton, UserView
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.database import InventoryManager, UserRecord
    from app.util.types import CommandResponse, TypedInteraction


class ItemTransformer(app_commands.Transformer):
    @classmethod
    async def convert(cls, _, value: str) -> Item:
        return query_item(value)

    async def transform(self, _, value: str) -> Item:
        return query_item(value)

    async def autocomplete(self, _, value: str) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name=item.name, value=item.key)
            for item in query_collection_many(Items, Item, value)
        ]


class DropView(discord.ui.View):
    def __init__(self, ctx: Context) -> None:
        super().__init__(timeout=120)

        self.ctx: Context = ctx
        self.winner: discord.Member | None = None

        self._lock: asyncio.Lock = asyncio.Lock()

    async def interaction_check(self, interaction: TypedInteraction) -> bool:
        if interaction.user == self.ctx.author:
            await interaction.response.send_message("You cannot claim your own drop, it's too late now!", ephemeral=True)

            return False

        return True

    @discord.ui.button(label='Claim!', style=discord.ButtonStyle.success)
    async def claim(self, interaction: TypedInteraction, button: discord.ui.Button) -> None:
        if self.winner:
            await interaction.response.send_message('This drop has already been claimed!', ephemeral=True)

        async with self._lock:
            self.winner = interaction.user
            button.disabled = True

            await interaction.response.edit_message(view=self)
            self.stop()

    async def on_timeout(self) -> None:
        assert self.children

        child = self.children[0]
        assert isinstance(child, discord.ui.Button)

        child.disabled = True


class RecipeSelect(discord.ui.Select['RecipeView']):
    def __init__(self, default: Recipe | None = None) -> None:
        super().__init__(
            placeholder='Choose a recipe...',
            options=[
                discord.SelectOption(
                    label=recipe.name,
                    value=recipe.key,
                    emoji=recipe.emoji,
                    description=cutoff(recipe.description, max_length=50, exact=True),
                    default=default == recipe,
                )
                for recipe in walk_collection(Recipes, Recipe)
            ],
            row=0,
        )

    async def callback(self, interaction: TypedInteraction) -> None:
        try:
            recipe = get_by_key(Recipes, self.values[0])
        except (KeyError, IndexError):
            return await interaction.response.send_message('Could not resolve that recipe for some reason.', ephemeral=True)

        self.view.current = recipe
        self.view.update()
        await interaction.response.edit_message(embed=self.view.build_embed(), view=self.view)


class RecipeView(UserView):
    def __init__(self, ctx: Context, record: UserRecord, default: Recipe | None = None) -> None:
        self.ctx: Context = ctx
        self.record: UserRecord = record

        self.current: Recipe = default or next(walk_collection(Recipes, Recipe))
        self.input_lock: asyncio.Lock = asyncio.Lock()

        super().__init__(ctx.author, timeout=60)
        self.add_item(RecipeSelect(default=default))

        self.update()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.current.name, description=self.current.description, color=Colors.primary, timestamp=self.ctx.now,
        )
        embed.set_author(name='Recipes', icon_url=self.ctx.author.display_avatar)
        embed.set_thumbnail(url=image_url_from_emoji(self.current.emoji))

        embed.add_field(name='General', value=dedent(f"""
            **Name:**: {self.current.name}
            **Query Key: `{self.current.key}`**
            **Price:** {Emojis.coin} {self.current.price:,}
        """), inline=False)

        embed.add_field(name='Ingredients', value='\n'.join(
            (f'{item.display_name} x{quantity}' for item, quantity in self.current.ingredients.items())
        ))
        return embed

    def update(self) -> None:
        amount = self._get_max()
        toggle = amount > 0

        for child in self.children:
            if child is self.stop_button or not isinstance(child, discord.ui.Button):
                continue

            child.disabled = not toggle
            child.style = discord.ButtonStyle.primary

        self.craft_max.label = f'Craft Max ({amount:,})' if amount > 0 else 'Craft Max'

    async def _craft(self, amount: int = 1, *, interaction: TypedInteraction = None) -> Any:
        respond_error = partial(interaction.response.send_message, ephemeral=True) if interaction else self.ctx.reply
        respond = interaction.response.send_message if interaction else self.ctx.reply
        extra = f' ({Emojis.coin} **{self.current.price * amount:,}** for {amount})' if amount > 1 else ''

        if self.record.wallet < self.current.price * amount:
            return await respond_error(
                f'Insufficient funds: Crafting one of this item costs {Emojis.coin} **{self.current.price:,}**{extra}, '
                f'you only have {Emojis.coin} **{self.record.wallet:,}**.',
            )

        manager = self.record.inventory_manager
        quantity_of = manager.cached.quantity_of

        if any(quantity_of(item) < quantity * amount for item, quantity in self.current.ingredients.items()):
            extra = ', maybe try a lower amount?' if amount > 1 else ''

            return await respond_error(
                f"You don't have enough of the required ingredients to craft this recipe{extra}",
            )

        async with self.record.db.acquire() as conn:
            await self.record.add(wallet=-self.current.price * amount, connection=conn)

            for item, quantity in self.current.ingredients.items():
                await manager.add_item(item, -quantity * amount, connection=conn)

            for item, quantity in self.current.result.items():
                await manager.add_item(item, quantity * amount, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=self.ctx.now)
        embed.set_author(name='Crafted Successfully', icon_url=self.ctx.author.display_avatar)

        embed.add_field(
            name='Crafted',
            value='\n'.join(f'{item.display_name} x{quantity * amount:,}' for item, quantity in self.current.result.items()),
            inline=False
        )

        embed.add_field(
            name='Ingredients Used',
            value=f'{Emojis.coin} {self.current.price * amount:,}\n' + '\n'.join(
                f'{item.display_name} x{quantity * amount:,}' for item, quantity in self.current.ingredients.items()
            ),
            inline=False,
        )
        await respond(embed=embed)

    def _get_max(self) -> int:
        inventory = self.record.inventory_manager

        item_max = min(inventory.cached.quantity_of(item) // quantity for item, quantity in self.current.ingredients.items())
        return min(item_max, self.record.wallet // self.current.price)

    @discord.ui.button(label='Craft One', style=discord.ButtonStyle.primary, row=1)
    async def craft_one(self, interaction: TypedInteraction, _) -> None:
        await self._craft(1, interaction=interaction)

    @discord.ui.button(label='Craft Max', style=discord.ButtonStyle.primary, row=1)
    async def craft_max(self, interaction: TypedInteraction, _) -> None:
        await self._craft(self._get_max(), interaction=interaction)

    @discord.ui.button(label='Craft Custom', style=discord.ButtonStyle.primary, row=1)
    async def craft_custom(self, interaction: TypedInteraction, _) -> Any:
        async with self.input_lock:
            await interaction.response.send_message(
                'How many of this item/recipe do you want to craft? Send a valid quantity in chat, e.g. "3" or "half".',
            )

            try:
                response = await self.ctx.bot.wait_for(
                    'message', timeout=30, check=lambda m: m.author == interaction.user,
                )
            except asyncio.TimeoutError:
                return await self.ctx.reply("You took too long to respond, cancelling.")

            maximum = self._get_max()
            try:
                await self._craft(get_amount(maximum, 1, maximum, response.content))
            except Exception as e:
                await self.ctx.reply(f'Error: {e.__class__.__name__}')

    @discord.ui.button(label='Stop', style=discord.ButtonStyle.danger, row=1)
    async def stop_button(self, interaction: TypedInteraction, button: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True

            if child is not button and isinstance(child, discord.ui.Button):
                child.style = discord.ButtonStyle.secondary

        self.stop()
        await interaction.response.edit_message(view=self)


if TYPE_CHECKING:
    query_item_type: TypeAlias = ItemType | None
else:
    def query_item_type(arg: str) -> ItemType | None:
        if arg.lower() in ('all', '*'):
            return None
        return query_collection(ItemType, ItemType, arg, get_key=lambda value: value.name)


TITLE = 0
DESCRIPTION = 1


def shop_paginator(
    ctx: Context,
    *,
    record: UserRecord,
    inventory: InventoryManager,
    type: ItemType | None = None,
    query: str | None = None,
) -> Paginator:
    fields = []
    embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
    query = query and query.lower()
    offset = query and len(query)

    for i in walk_collection(Items, Item):
        if not i.buyable:
            continue
        if type is not None and i.type is not type:
            continue

        loc = match_loc = None
        if query is not None:
            if (loc := i.name.lower().find(query)) != -1:
                match_loc = TITLE
            elif i.key.find(query) != -1:
                pass
            elif (loc := i.description.lower().find(query)) != -1 and len(i.description) + offset < 100:
                match_loc = DESCRIPTION
            else:
                continue

        embed.title = 'Item Shop'
        embed.description = f'To buy an item, see `{ctx.clean_prefix}buy`.\nTo view information on an item, see `{ctx.clean_prefix}iteminfo`.'

        comment = '*You cannot afford this item.*\n' if i.price > record.wallet else ''
        owned = inventory.cached.quantity_of(i)
        owned = f'(You own {owned:,})' if owned else ''

        description = cutoff(i.brief, max_length=100)

        end = loc and loc + offset
        name = i.name

        if match_loc == TITLE:
            name = f'{name[:loc]}**{name[loc:end]}**{name[end:]}'
        elif match_loc == DESCRIPTION:
            description = f'{description[:loc]}**{description[loc:end]}**{description[end:]}'

        fields.append({
            'name': f'• {i.emoji} {name} — {Emojis.coin} {i.price:,} {owned}',
            'value': comment + description,
            'inline': False,
        })
    fields = fields or [{
        'name': 'No items found!',
        'value': f'No items found for query: `{query}`',
        'inline': False,
    }]

    return Paginator(
        ctx,
        FieldBasedFormatter(embed, fields, per_page=5),
        other_components=[ShopCategorySelect(ctx, record=record, inventory=inventory)],
        row=1,
    )


class ShopSearchModal(discord.ui.Modal):
    query = discord.ui.TextInput(
        label='Search Query',
        placeholder='Enter a search query for the item shop... (e.g. "spinning coin")',
        min_length=2,
        max_length=50,
    )

    def __init__(self) -> None:
        super().__init__(timeout=60, title='Item Search')
        self.interaction: TypedInteraction | None = None

    async def on_submit(self, interaction: TypedInteraction) -> None:
        self.interaction = interaction


class ShopCategorySelect(discord.ui.Select):
    OPTIONS = [
        discord.SelectOption(label='All Items', value='all'),
        *(
            discord.SelectOption(label=category.name.title(), value=str(category.value))
            for category in walk_collection(ItemType, ItemType)
            if any(item.type is category and item.buyable for item in walk_collection(Items, Item))
        ),
        discord.SelectOption(label='Search...', value='search'),
    ]

    def __init__(
        self,
        ctx: Context,
        *,
        record: UserRecord,
        inventory: InventoryManager,
    ) -> None:
        super().__init__(
            placeholder='Filter by category...',
            options=self.OPTIONS,
            row=0,
        )
        self.ctx = ctx
        self.record = record
        self.inventory = inventory

    async def callback(self, interaction: TypedInteraction) -> None:
        value = self.values[0]
        if value == 'all':
            query_type = None
            query_search = None
        elif value == 'search':
            query_type = None
            modal = ShopSearchModal()
            await interaction.response.send_modal(modal)
            await modal.wait()
            query_search = modal.query.value
            interaction = modal.interaction
        else:
            query_type = ItemType(int(value))
            query_search = None

        paginator = shop_paginator(
            self.ctx, record=self.record, inventory=self.inventory,
            type=query_type, query=query_search,
        )
        await paginator.start(edit=True, interaction=interaction)


class BankTransactionModal(CommandInvocableModal):
    amount = discord.ui.TextInput(
        label='How many coins do you want to {}?',
        placeholder='Enter a number or shorthand like "500", "10k", or "all"...',
        required=True,
        min_length=1,
        max_length=20,
    )

    def __init__(self, cmd: Command | HybridCommand, *, title: str, transaction: Literal[0, 1]) -> None:
        super().__init__(title=title, command=cmd)
        self.amount.label = self.amount.label.format('withdraw' if transaction == WITHDRAW else 'deposit')
        self.converter = BankTransaction(transaction)()

    async def on_submit(self, interaction: TypedInteraction, /) -> None:
        ctx = await self.get_context(interaction)
        try:
            value = await self.converter.convert(ctx, self.amount.value)
        except commands.CommandError as exc:
            await ctx.command.dispatch_error(ctx, exc)
        else:
            await self.invoke(ctx, amount=value)


class Transactions(Cog):
    """Commands that handle transactions between the bank or other users."""

    emoji = '\U0001f91d'

    @discord.utils.cached_property
    def withdraw_modal(self) -> Callable[[TypedInteraction], BankTransactionModal]:
        return lambda _: BankTransactionModal(self.withdraw, title='Withdraw Coins', transaction=WITHDRAW)

    @discord.utils.cached_property
    def deposit_modal(self) -> Callable[[TypedInteraction], BankTransactionModal]:
        return lambda _: BankTransactionModal(self.deposit, title='Deposit Coins', transaction=DEPOSIT)

    # noinspection PyTypeChecker
    @command(aliases={"w", "with", "wd"}, hybrid=True)
    @app_commands.describe(
        amount='The amount of coins to withdraw from your bank. Use "all" to withdraw all coins.'
    )
    @simple_cooldown(1, 8)
    @lock_transactions
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

        view = discord.ui.View(timeout=60)
        view.add_item(ModalButton(
            modal=self.withdraw_modal, label='Withdraw More Coins', style=discord.ButtonStyle.primary,
            disabled=not data.bank,
        ))
        view.add_item(ModalButton(modal=self.deposit_modal, label='Deposit Coins', style=discord.ButtonStyle.primary))

        return embed, view, REPLY

    # noinspection PyTypeChecker
    @command(aliases={"d", "dep"}, hybrid=True)
    @app_commands.describe(
        amount='The amount of coins to deposit into your bank. Use "all" to deposit all coins.'
    )
    @simple_cooldown(1, 8)
    @lock_transactions
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

        view = discord.ui.View(timeout=60)
        view.add_item(ModalButton(
            modal=self.deposit_modal, label='Deposit More Coins', style=discord.ButtonStyle.primary,
            disabled=not data.wallet,
        ))
        view.add_item(ModalButton(
            modal=self.withdraw_modal, label='Withdraw Coins', style=discord.ButtonStyle.primary,
        ))

        return embed, view, REPLY

    @command(aliases={"store", "market", "sh", "iteminfo", "ii", "item"}, hybrid=True)
    @app_commands.describe(item='The item to view specific information on.')
    @simple_cooldown(1, 6)
    async def shop(self, ctx: Context, *, item: query_item = None) -> CommandResponse:
        """View the item shop, or view information on a specific item.

        Arguments:
        - `item`: The item to view information on. Leave blank to the view the item shop.
        """
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        record = await ctx.db.get_user_record(ctx.author.id)

        inventory = record.inventory_manager
        await inventory.wait()

        if not item:
            paginator = shop_paginator(ctx, record=record, inventory=inventory)
            return paginator, REPLY

        item: Item
        owned = inventory.cached.quantity_of(item)

        embed.title = f'{item.display_name} ({owned} owned)'
        embed.description = item.description

        embed.set_thumbnail(url=image_url_from_emoji(item.emoji))

        embed.add_field(name='General', value=dedent(f"""
            Name: {item.get_display_name(bold=True)}
            Query Key: **`{item.key}`**
            Type: **{item.type.name.title()}**
            Rarity: **{item.rarity.name.title()}**
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

    @command(alias='purchase', hybrid=True, with_app_command=False)
    @simple_cooldown(3, 8)
    @user_max_concurrency(1)
    @lock_transactions
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

        money_back = 0
        money_back_text = []

        pets = record.pet_manager
        if hamster := pets.get_active_pet(Pets.hamster):
            factor = 0.005 + hamster.level * 0.001
            money_back += round(price * factor)
            money_back_text.append(
                f'Your **{Pets.hamster.display}** finds you {Emojis.coin} **{money_back:,}** coins back!',
            )

        async with ctx.db.acquire() as conn:
            await record.add_random_exp(10, 15, chance=0.5, ctx=ctx, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.5, connection=conn)

            await record.add(wallet=-price + money_back, connection=conn)
            await inventory.add_item(item, quantity, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)
        embed.description = f'You bought {item.get_sentence_chunk(quantity)} for {Emojis.coin} **{price:,}** coins.'
        embed.set_author(name=f'Successful Purchase: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_thumbnail(url=image_url_from_emoji(item.emoji))

        if money_back and money_back_text:
            embed.add_field(name='Money Back', value='\n'.join(f'- {line}' for line in money_back_text), inline=False)

        return embed, REPLY

    @buy.define_app_command()
    @app_commands.describe(
        item='The item to purchase.',
        quantity='How many of the item to purchase. Use "max" to buy as many as possible.',
    )
    async def buy_app_command(
        self,
        ctx: HybridContext,
        item: app_commands.Transform[Item, ItemTransformer],
        quantity: str = '1',
    ) -> None:
        transformed = await transform_item_and_quantity(ctx, BUY, item, quantity)
        await ctx.full_invoke(item_and_quantity=transformed)

    @command(alias='s', hybrid=True, with_app_command=False)
    @simple_cooldown(3, 8)
    @user_max_concurrency(1)
    @lock_transactions
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
            await record.add_random_exp(10, 15, chance=0.4, ctx=ctx, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.4, connection=conn)

            await record.add(wallet=value, connection=conn)
            await inventory.add_item(item, -quantity, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)
        embed.description = f'You sold {item.get_sentence_chunk(quantity)} in exchange for {Emojis.coin} **{value:,}** coins.'
        embed.set_author(name=f'Successful Transaction: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_thumbnail(url=image_url_from_emoji(item.emoji))

        return embed, REPLY

    @sell.define_app_command()
    @app_commands.describe(
        item='The item to sell',
        quantity='How many of the item to sell. Use "all" to sell all.',
    )
    async def sell_app_command(
        self,
        ctx: HybridContext,
        item: app_commands.Transform[Item, ItemTransformer],
        quantity: str = '1',
    ) -> None:
        transformed = await transform_item_and_quantity(ctx, SELL, item, quantity)
        await ctx.full_invoke(item_and_quantity=transformed)

    @command(aliases={'u', 'consume', 'activate', 'open'}, hybrid=True, with_app_command=False)
    @simple_cooldown(2, 10)
    @user_max_concurrency(1)
    @lock_transactions
    async def use(self, ctx: Context, *, item_and_quantity: ItemAndQuantityConverter(USE)):
        """Use the items you own!"""
        item, quantity = item_and_quantity
        record = await ctx.db.get_user_record(ctx.author.id)

        async with ctx.db.acquire() as conn:
            await record.add_random_exp(10, 15, chance=0.5, ctx=ctx, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.4, connection=conn)

            quantity = await item.use(ctx, quantity)

            if quantity > 0 and item.dispose:
                await record.inventory_manager.add_item(item, -quantity, connection=conn)

        await ctx.thumbs()

    @use.define_app_command()
    @app_commands.describe(
        item='The item to use',
        quantity='How many of the item to use. Note that some items may not be usable in bulk.',
    )
    async def use_app_command(
        self,
        ctx: HybridContext,
        item: app_commands.Transform[Item, ItemTransformer],
        quantity: str = '1',
    ) -> None:
        transformed = await transform_item_and_quantity(ctx, USE, item, quantity)
        await ctx.full_invoke(item_and_quantity=transformed)

    @command(aliases={'rm', 'dispose', 'deactivate', 'discard'}, hybrid=True)
    @simple_cooldown(2, 10)
    @user_max_concurrency(1)
    @lock_transactions
    async def remove(self, ctx: Context, *, item: query_item):
        """Remove the effects of active items."""
        record = await ctx.db.get_user_record(ctx.author.id)

        async with ctx.db.acquire() as conn:
            await record.add_random_exp(10, 15, chance=0.4, ctx=ctx, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.4, connection=conn)

            await item.remove(ctx)

        await ctx.thumbs()

    @shop.autocomplete('item')
    @remove.autocomplete('item')
    async def item_autocomplete(self, _, current: str) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name=item.name, value=item.key)
            for item in query_collection_many(Items, Item, current)
        ]

    @command(aliases={'give', 'gift', 'donate', 'pay'})
    @simple_cooldown(1, 30)
    @user_max_concurrency(1)
    @lock_transactions
    async def share(self, ctx: Context, user: CaseInsensitiveMemberConverter, *, entity: DropAmount | ItemAndQuantityConverter(DROP)):
        """Share coins or items from your inventory with another user."""
        if user.bot:
            return 'You cannot share with bots.', REPLY

        if user == ctx.author:
            return 'Sharing with yourself, that sounds kinda funny', REPLY

        if isinstance(entity, int):
            entity_human = f'{Emojis.coin} **{entity:,}**'
        else:
            item, quantity = entity
            entity_human = item.get_sentence_chunk(quantity)

        if not await ctx.confirm(
            f"Are you sure you want to give {entity_human} to {user.mention}?",
            allowed_mentions=discord.AllowedMentions.none(),
            reference=ctx.message,
            delete_after=True,
        ):
            return 'Cancelled transaction.', REPLY

        record = await ctx.db.get_user_record(ctx.author.id)
        their_record = await ctx.db.get_user_record(user.id)

        async with ctx.db.acquire() as conn:
            if isinstance(entity, int):
                await record.add(wallet=-entity, connection=conn)
                await their_record.add(wallet=entity, connection=conn)

                updated = f'{Emojis.coin} **{record.wallet:,}**', f'{Emojis.coin} **{their_record.wallet:,}**'
            else:
                # noinspection PyUnboundLocalVariable
                await record.inventory_manager.add_item(item, -quantity, connection=conn)
                await their_record.inventory_manager.add_item(item, quantity, connection=conn)

                updated = (
                    f'{item.emoji} {item.name} x{record.inventory_manager.cached.quantity_of(item):,}',
                    f'{item.emoji} {item.name} x{their_record.inventory_manager.cached.quantity_of(item):,}',
                )

            await their_record.notifications_manager.add_notification(
                title='You got coins!' if isinstance(entity, int) else 'You got items!',
                content=f'{ctx.author.mention} gave you {entity_human}.',
                connection=conn,
            )

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)
        embed.description = f'You gave {entity_human} to {user.mention}.'
        embed.set_author(name=f'Successful Transaction: {ctx.author}', icon_url=ctx.author.avatar.url)

        us, them = updated
        embed.add_field(name='Updated Values', value=f'{ctx.author.name}: {us}\n{user.name}: {them}')

        return embed, REPLY

    @command(aliases={'giveaway'})
    @simple_cooldown(1, 30)
    @user_max_concurrency(1)
    @lock_transactions
    async def drop(self, ctx: Context, *, entity: DropAmount | ItemAndQuantityConverter(DROP)):
        """Drop coins or items from your inventory into the chat.

        The first one to click the button will retrieve your coins!
        If no one clicks the button within 120 seconds, your coins/items will be returned.
        """
        record = await ctx.db.get_user_record(ctx.author.id)

        if isinstance(entity, int):
            await record.add(wallet=-entity)
        else:
            item, quantity = entity

            inventory = await record.inventory_manager.wait()
            await inventory.add_item(item, -quantity)

        entity_type = 'coins' if isinstance(entity, int) else 'items'

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name} has dropped {entity_type}!', icon_url=ctx.author.avatar.url)

        # noinspection PyUnboundLocalVariable
        entity_human = f"{Emojis.coin} {entity:,}" if isinstance(entity, int) else item.get_sentence_chunk(quantity)
        embed.description = f'{ctx.author.mention} has dropped {entity_human}!'

        embed.set_footer(text=f'Click the button below to retrieve your {entity_type}!')

        view = DropView(ctx)
        yield embed, view, REPLY, NO_EXTRA

        embed.set_footer(text='')

        await view.wait()
        if not view.winner:
            if isinstance(entity, int):
                await record.add(wallet=entity)
            else:
                # noinspection PyUnboundLocalVariable
                await inventory.add_item(item, quantity)

            embed.description = 'No one clicked the button! Your entities have been returned.'
            embed.colour = Colors.error

            yield embed, view, EDIT
            return

        winner_record = await ctx.db.get_user_record(view.winner.id)
        if isinstance(entity, int):
            await winner_record.add(wallet=entity)
        else:
            # noinspection PyUnboundLocalVariable
            await winner_record.inventory_manager.add_item(item, quantity)

        embed.colour = Colors.success
        embed.description = f'{view.winner.mention} was the first one to click the button! They have received {entity_human}.'
        embed.set_author(name=f'Winner: {view.winner}', icon_url=view.winner.avatar.url)

        yield embed, view, EDIT

    @command(aliases={'recipes', 'exchange', 'recipe', 'rc'}, hybrid=True)
    @simple_cooldown(1, 4)
    @user_max_concurrency(1)
    async def craft(self, ctx: Context, *, recipe: query_recipe = None):
        """Craft items from your inventory to make new ones!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        await record.inventory_manager.wait()

        view = RecipeView(ctx, record, default=recipe)
        yield view.build_embed(), view, REPLY

        await view.wait()

    @craft.autocomplete('recipe')
    async def recipe_autocomplete(self, _, current: str) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name=recipe.name, value=recipe.key)
            for recipe in query_collection_many(Recipes, Recipe, current)
        ]

    PRESTIGE_WHAT_DO_I_LOSE = (
        '- Your wallet, bank, and bank space will be wiped.\n'
        '- Your inventory will be wiped, except for:\n'
        '  - Any collectibles,\n'
        '  - Any crates, and\n'
        '  - Any items of **Mythic** rarity.\n'
        '- All crops will be wiped on your farm, however you will keep all claimed land.'
    )
    PRESTIGE_WHAT_DO_I_KEEP = (
        '- You keep the aforementioned subset of items in your inventory,\n'
        '- All claimed land on your farm,\n'
        '- All skills and training progress,\n'
        '- All pets and their levels,\n'
        '- All crafting recipes you have discovered, and\n'
        '- Any non-tangible entities such as notifications and cooldowns.'
    )

    @command(aliases={'pres', 'pr', 'prest', 'rebirth'}, hybrid=True)
    @simple_cooldown(1, 10)
    async def prestige(self, ctx: Context) -> CommandResponse:
        """Prestige and start over in exchange for long-term multipliers."""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()

        current_emoji = Emojis.get_prestige_emoji(record.prestige, trailing_ws=True)
        next_emoji = Emojis.get_prestige_emoji(next_prestige := record.prestige + 1, trailing_ws=True)

        level_requirement = next_prestige * 20
        meets_level = record.level >= level_requirement

        bank_requirement = next_prestige * 50_000
        meets_bank = record.bank >= bank_requirement

        unique_items = sum(value > 0 for value in inventory.cached.values())
        unique_items_requirement = min(38 + next_prestige * 2, len(list(Items.all())) - 4)
        meets_unique_items = unique_items >= unique_items_requirement

        _ = lambda b: Emojis.enabled if b else Emojis.disabled
        progress = lambda ratio: f'{progress_bar(ratio)} ({min(ratio, 1.0):.1%})'
        embed = discord.Embed(
            color=Colors.primary,
            timestamp=ctx.now,
            description=(
                f'Current prestige level: {current_emoji} **{record.prestige}**\n'
                f'Next prestige level: {next_emoji} **{next_prestige}**'
            ),
        )
        embed.set_author(name=f'Prestige: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.add_field(
            name=f'{_(meets_level)} Level **{record.level}**/{level_requirement:,}',
            value=progress(record.level / level_requirement),
            inline=False,
        )
        embed.add_field(
            name=f'{_(meets_bank)} Coins in Bank: {Emojis.coin} **{record.bank:,}**/{bank_requirement:,}',
            value=progress(record.bank / bank_requirement),
            inline=False,
        )
        embed.add_field(
            name=f'{_(meets_unique_items)} Unique Items: **{unique_items}**/{unique_items_requirement}',
            value=progress(unique_items / unique_items_requirement),
            inline=False,
        )
        if meets_level and meets_bank and meets_unique_items:
            embed.set_footer(text='You meet all requirements to prestige!')
            view = PrestigeView(ctx, record=record, next_prestige=next_prestige)
            return embed, view, REPLY

        view = discord.ui.View(timeout=1)  # timeout=0 gives weird problems
        view.add_item(
            discord.ui.Button(
                label='You do not meet prestige requirements yet.',
                style=discord.ButtonStyle.secondary,
                disabled=True,
            ),
        )
        return embed, view, REPLY


class PrestigeView(UserView):
    def __init__(self, ctx: Context, *, record: UserRecord, next_prestige: int) -> None:
        super().__init__(ctx.author, timeout=60)
        self.ctx = ctx
        self.record: UserRecord = record
        self.inventory: InventoryManager = record.inventory_manager
        self.next_prestige = next_prestige
        self.prestige.emoji = self.emoji = Emojis.get_prestige_emoji(next_prestige)

    @discord.ui.button(label='Prestige!', style=discord.ButtonStyle.primary)
    async def prestige(self, interaction: TypedInteraction, _button: discord.ui.Button) -> None:
        lock = self.ctx.bot.transaction_locks.setdefault(self.ctx.author.id, LockWithReason())
        if lock.locked():
            reason = f' ({lock.reason})' if lock.reason else ''
            return await interaction.response.send_message(
                f'You are already performing a transaction{reason}. '
                'Please finish it or wait until it is finished before prestiging.',
                ephemeral=True,
            )

        async with lock:
            await self._prestige(interaction, _button)

    async def _prestige(self, interaction: TypedInteraction, _button: discord.ui.Button) -> None:
        crate = Items.mythic_crate if self.next_prestige % 5 == 0 else Items.legendary_crate
        receive = (
            f'- {Items.banknote.get_sentence_chunk(self.next_prestige)},\n'
            f'- {crate.get_sentence_chunk()},\n'
            f'- {self.next_prestige * 50}% faster bank space gain,\n'
            f'- {self.next_prestige * 25}% XP multiplier,\n'
            f'- {self.next_prestige * 25}% coin multiplier,\n'
            f'- {self.next_prestige * 10}% extra space from banknotes, and\n'
            f'- {self.emoji} **Prestige {self.next_prestige}** badge'
        )
        message = (
            f'You are about to prestige to {self.emoji} **Prestige {self.next_prestige}**!\n\n'
            'Prestiging is required to get far into the economy. '
            'With it, you gain perks, multipliers, and increased limits that are unobtainable without doing so.\n'
            '## What will I lose?\n'
            f'{Transactions.PRESTIGE_WHAT_DO_I_LOSE}\n'
            '## What will I keep?\n'
            f'{Transactions.PRESTIGE_WHAT_DO_I_KEEP}\n'
            f'## What will I get in exchange for prestiging?\n{receive}'
        )

        view = ConfirmationView(user=self.ctx.author, true="Yes, let's prestige!", false='Maybe next time', timeout=120)
        if not await self.ctx.confirm(message, interaction=interaction, view=view):
            self.stop()
            return await view.interaction.response.send_message('Okay, we will postpone your prestige.', ephemeral=True)

        async with self.ctx.db.acquire() as conn:
            keep = {
                item.key: quantity
                for item, quantity in self.inventory.cached.items()
                if quantity > 0 and (
                    item.type in (ItemType.collectible, ItemType.crate)
                    or item.rarity is ItemRarity.mythic
                )
            }
            await self.record.update(
                wallet=0, bank=0, max_bank=100, prestige=self.next_prestige, connection=conn,
            )
            inventory = self.record.inventory_manager
            await inventory.wipe(connection=conn)
            await self.record.crop_manager.wipe_keeping_land(connection=conn)

            # Replenish promised items
            await inventory.update(**keep)
            await inventory.add_item(Items.banknote, self.next_prestige, connection=conn)
            await inventory.add_item(Items.legendary_crate, 1, connection=conn)

        self.stop()
        await view.interaction.response.send_message(
            f'\U0001f389 What a legend, after prestiging you are now {self.emoji} **Prestige {self.next_prestige}**.\n'
            f'## You have received:\n{receive}'
        )


setup = Transactions.simple_setup

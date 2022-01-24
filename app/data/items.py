from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Generator, TYPE_CHECKING, TypeAlias

from app.util.common import pluralize
from config import Emojis

if TYPE_CHECKING:
    from app.core import Context

    UsageCallback: TypeAlias = 'Callable[[Items, Context, Item], Awaitable[Any]] | Callable[[Items, Context, Item, int], Awaitable[Any]]'
    RemovalCallback: TypeAlias = 'Callable[[Items, Context, Item], Awaitable[Any]]'


class ItemType(Enum):
    """Stores the type of this item."""
    tool = 0


@dataclass
class Item:
    """Stores data about an item."""
    type: ItemType
    key: str
    name: str
    emoji: str
    description: str
    price: int = None
    sell: int = None
    buyable: bool = False
    sellable: bool = True
    giftable: bool = True
    dispose: bool = False  # Dispose on use?
    singular: str = None
    plural: str = None
    metadata: Any | None = None

    usage_callback: UsageCallback | None = None
    removal_callback: RemovalCallback | None = None

    def __post_init__(self) -> None:
        if not self.singular:
            self.singular = 'an' if self.name.lower().startswith(tuple('aeiou')) else 'a'

        if self.sell and not self.price:
            self.price = self.sell

        elif self.price and not self.sell:
            self.sell = round(self.price / 2.7)

        if not self.plural:
            self.plural = self.name + 's'

    def __hash__(self) -> int:
        return hash(self.key)

    def __str__(self) -> str:
        return self.key

    def __repr__(self) -> str:
        return f'<Item key={self.key} name={self.name!r}>'

    @property
    def display_name(self) -> str:
        return self.get_display_name()

    @property
    def usable(self) -> bool:
        return self.usage_callback is not None

    @property
    def removable(self) -> bool:
        return self.removal_callback is not None

    def get_sentence_chunk(self, quantity: int = 1, *, bold: bool = True) -> str:
        fmt = '{} **{}**' if bold else '{} {}'
        name = self.name if quantity == 1 else self.plural
        middle = fmt.format(self.emoji, name).strip()

        quantifier = format(quantity, ',') if quantity != 1 else self.singular
        return f'{quantifier} {middle}'

    def get_display_name(self, *, bold: bool = False) -> str:
        fmt = '{} **{}**' if bold else '{} {}'
        return fmt.format(self.emoji, self.name).strip()

    def to_use(self, func: UsageCallback) -> UsageCallback:
        self.usage_callback = func
        return func

    def to_remove(self, func: RemovalCallback) -> RemovalCallback:
        self.removal_callback = func
        return func

    async def use(self, ctx: Context, quantity: int) -> int:
        assert self.usable

        try:
            coro = self.usage_callback(ITEMS_INST, ctx, self, quantity)
        except TypeError:
            coro = self.usage_callback(ITEMS_INST, ctx, self)
            quantity = 1

        await coro
        return quantity

    async def remove(self, ctx: Context) -> None:
        assert self.removable

        await self.removal_callback(ITEMS_INST, ctx, self)


class Items:
    """Stores all items"""

    lifesaver = Item(
        type=ItemType.tool,
        key='lifesaver',
        name='Lifesaver',
        emoji='<:lifesaver:934608079947964447>',
        description='These quite literally save your life.',
        price=4200,
        buyable=True,
    )

    banknote = Item(
        type=ItemType.tool,
        key='banknote',
        name='Banknote',
        emoji='<:banknote:934913052174848040>',
        description='You can sell these for coins, or use these in order to expand your bank space. Gives between 1,000 to 3,000 bank space.',
        sell=10000,
        dispose=True,
    )

    @banknote.to_use
    async def banknote_use(self, ctx: Context, item: Item, quantity: int) -> None:
        message = await ctx.message.reply(pluralize(f'{item.emoji} Using {quantity} banknote(s)...'))

        await asyncio.sleep(random.uniform(2, 4))

        profit = random.randint(1000 * quantity, 3000 * quantity)
        await ctx.db.get_user_record(ctx.author.id, fetch=False).add(max_bank=profit)

        await message.edit(content=pluralize(
            f'{item.emoji} Your {quantity} banknote(s) expanded your bank space by {Emojis.coin} **{profit:,}**.'
        ))

    @classmethod
    def all(cls) -> Generator[Item, Any, Any]:
        """Lazily iterates through all items."""
        for attr in dir(cls):
            if isinstance(item := getattr(cls, attr), Item):
                yield item


ITEMS_INST = Items()

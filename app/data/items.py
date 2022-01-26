from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import Enum
from functools import partial
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
    fish = 1


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


Fish = partial(Item, type=ItemType.fish)


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

    fishing_pole = Item(
        type=ItemType.tool,
        key='fishing_pole',
        name='Fishing Pole',
        emoji='<:fishing_pole:935298127353745499>',
        description='Owning this will grant you access to the `fish` command - fish for fish and sell them for profit!',
        price=10000,
        buyable=True,
    )

    fish_bait = Item(
        type=ItemType.tool,
        key='fish_bait',
        name='Fish Bait',
        emoji='\U0001fab1',
        description='When you fish while owning this, your chances of catching rarer fish will increase. Disposed every time you fish, no matter success or fail.',
        price=300,
        buyable=True,
    )

    @fishing_pole.to_use
    async def use_fishing_pole(self, ctx: Context, _) -> None:
        await ctx.invoke(ctx.bot.get_command('fish'))

    fish = Fish(
        key='fish',
        name='Fish',
        plural='Fish',
        emoji='<:fish:935002348361748491>',
        description='A normal fish. Commonly found in the ocean.',
        sell=100,
    )

    sardine = Fish(
        key='sardine',
        name='Sardine',
        emoji='<:sardine:935265248091451493>',
        description='A nutritious fish. They are small and easy to catch.',
        sell=150,
    )

    angel_fish = Fish(
        key='angel_fish',
        name='Angel Fish',
        plural='Angel Fish',
        emoji='<:angel_fish:935265295000551475>',
        description='Angelfish are tropical freshwater fish that come in a variety of colors.',
        sell=250,
    )

    blowfish = Fish(
        key='blowfish',
        name='Blowfish',
        plural='Blowfish',
        emoji='<:blowfish:935265366601498685>',
        description='These are also known as pufferfish. These are caught in it\'s inflated form.',
        sell=350,
    )

    crab = Fish(
        key='crab',
        name='Crab',
        emoji='<:crab:935285322395299840>',
        description='Crabs are crustaceans that are found in the ocean. Also the mascot of the Rust programming language.',
        sell=450,
    )

    lobster = Fish(
        key='lobster',
        name='Lobster',
        emoji='<:lobster:935288666283212830>',
        description='Lobsters are large crustaceans that are found in the ocean.',
        sell=575,
    )

    octopus = Fish(
        key='octopus',
        name='Octopus',
        plural='Octopuses',
        emoji='<:octopus:935292291143331900>',
        description='Octopuses have 3 hearts and 9 brains. And yes, that is the correct plural form of octopus.',
        sell=800,
    )

    dolphin = Fish(
        key='dolphin',
        name='Dolphin',
        emoji='<:dolphin:935294203364245601>',
        description='Dolphins are large aquatic mammals that are found in the ocean.',
        sell=1050,
    )

    shark = Fish(
        key='shark',
        name='Shark',
        emoji='<:shark:935301959949365249>',
        description='Sharks are large predatory fish that are found in the ocean.',
        sell=1250,
    )

    whale = Fish(
        key='whale',
        name='Whale',
        emoji='<:whale:935305582846566410>',
        description='Whales are huge mammals that swim deep in the ocean. How do you even manage to catch these?',
        sell=1760,
    )

    axolotl = Fish(
        key='axolotl',
        name='Axolotl',
        emoji='<:axolotl:935691745180667944>',
        description='The cool salamander',
        sell=3000,
    )

    vibe_fish = Fish(
        key='vibe_fish',
        name='Vibe Fish',
        plural='Vibe Fish',
        emoji='<a:vibe_fish:935293751604183060>',
        description='\uff56\uff49\uff42\uff45',  # "vibe" in full-width text
        sell=6500,
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

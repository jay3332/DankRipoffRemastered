from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from functools import partial
from textwrap import dedent
from typing import Any, Awaitable, Callable, Generator, Generic, NamedTuple, TYPE_CHECKING, TypeAlias, TypeVar

from discord.ext.commands import BadArgument

from app.util.common import pluralize
from config import Emojis

if TYPE_CHECKING:
    from app.core import Context

    UsageCallback: TypeAlias = 'Callable[[Items, Context, Item], Awaitable[Any]] | Callable[[Items, Context, Item, int], Awaitable[Any]]'
    RemovalCallback: TypeAlias = 'Callable[[Items, Context, Item], Awaitable[Any]]'

T = TypeVar('T')


class ItemType(Enum):
    """Stores the type of this item."""
    tool = 0
    fish = 1
    wood = 2
    crate = 3
    collectible = 4
    worm = 5
    ore = 6


class ItemRarity(Enum):
    common = 0
    uncommon = 1
    rare = 2
    epic = 3
    legendary = 4
    mythic = 5
    unobtainable = 6


class CrateMetadata(NamedTuple):
    minimum: int
    maximum: int
    items: dict[Item, tuple[float, int, int]]


@dataclass
class Item(Generic[T]):
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
    rarity: ItemRarity = ItemRarity.common
    metadata: T = None

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

        try:
            await coro
        except ItemUsageError as exc:
            await ctx.send(exc, reference=ctx.message)
            return 0

        return quantity

    async def remove(self, ctx: Context) -> None:
        assert self.removable

        await self.removal_callback(ITEMS_INST, ctx, self)


class ItemUsageError(Exception):
    """When raised, disposed items will not be disposed."""


Fish = partial(Item, type=ItemType.fish)
Wood = partial(Item, type=ItemType.wood)
Crate: Callable[..., Item[CrateMetadata]] = partial(Item, type=ItemType.crate, dispose=True)
Worm = partial(Item, type=ItemType.worm)
Ore = partial(Item, type=ItemType.ore)


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

    padlock = Item(
        type=ItemType.tool,
        key='padlock',
        name='Padlock',
        emoji='<:padlock:785630994685755424>',
        description='Add a layer of protection to your wallet! When used, others will pay a fine when they try to rob you.',
        price=5000,
        buyable=True,
        dispose=True,
    )

    @padlock.to_use
    async def use_padlock(self, ctx: Context, item: Item) -> None:
        record = await ctx.db.get_user_record(ctx.author.id)
        if record.padlock_active:
            raise ItemUsageError('You already have a padlock active!')

        await record.update(padlock_active=True)

        await ctx.send(f'{item.emoji} Successfully activated your padlock.')

    @padlock.to_remove
    async def remove_padlock(self, ctx: Context, item: Item) -> None:
        record = await ctx.db.get_user_record(ctx.author.id)
        if not record.padlock_active:
            raise BadArgument('You do not have a padlock active!')

        await record.update(padlock_active=False)
        await ctx.send(f'{item.emoji} Successfully deactivated your padlock.')

    banknote = Item(
        type=ItemType.tool,
        key='banknote',
        name='Banknote',
        emoji='<:banknote:934913052174848040>',
        description='You can sell these for coins, or use these in order to expand your bank space. Gives between 1,000 to 3,000 bank space.',
        sell=10000,
        rarity=ItemRarity.uncommon,
        dispose=True,
    )

    @banknote.to_use
    async def use_banknote(self, ctx: Context, item: Item, quantity: int) -> None:
        message = await ctx.message.reply(pluralize(f'{item.emoji} Using {quantity} banknote(s)...'))

        await asyncio.sleep(random.uniform(2, 4))

        profit = random.randint(1000 * quantity, 3000 * quantity)
        await ctx.db.get_user_record(ctx.author.id, fetch=False).add(max_bank=profit)

        await message.edit(content=pluralize(
            f'{item.emoji} Your {quantity} banknote(s) expanded your bank space by {Emojis.coin} **{profit:,}**.'
        ))

    cheese = Item(
        type=ItemType.tool,
        key='cheese',
        name='Cheese',
        plural='Cheese',
        emoji='<:cheese:937157036737724477>',
        description=(
            'A lucsious slice of cheese. Eating (using) these will increase your permanent EXP multiplier. '
            'There is a super small chance (2% per slice of cheese) you could die from lactose intolerance, though.\n\n'
            'It is preferred to use these individually rather than in bulk.'
        ),
        price=7500,
        buyable=True,
        dispose=True,
    )

    @cheese.to_use
    async def use_cheese(self, ctx: Context, item: Item, quantity: int) -> None:
        if quantity > 10:
            raise ItemUsageError('You can only eat up to 10 slices of cheese at a time.')

        record = await ctx.db.get_user_record(ctx.author.id)

        if quantity == 1:
            readable = f'a slice of {item.name}'
        else:
            readable = f'{quantity:,} slices of {item.name}'

        original = await ctx.send(f'{item.emoji} Eating {readable}...', reference=ctx.message)
        await asyncio.sleep(random.uniform(2, 4))

        chance = 1 - 0.98 ** quantity
        if random.random() < chance:
            await record.make_dead(reason='lactose intolerance from eating cheese')
            await original.edit(
                content=f'{item.emoji} You eat the cheese only to find out that you are lactose intolerant, and now you\'re dead.'
            )

            return

        # 0.1% to 1% per slice
        gain = random.uniform(0.001 * quantity, 0.01 * quantity)
        await record.add(exp_multiplier=gain)

        await original.edit(content=dedent(f'''
            {item.emoji} You ate {readable} and gained a **{gain:.02%}** EXP multiplier.
            You now have a **{record.exp_multiplier:.02%}** EXP multiplier.
        '''))

    spinning_coin = Item(
        type=ItemType.collectible,
        key='spinning_coin',
        name='Spinning Coin',
        emoji='<a:spinning_coin:939937188836147240>',
        description='A coin but it spins automatically, cool isn\'t it?',
        price=500_000,
        rarity=ItemRarity.epic,
        buyable=True,
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

    stick = Item(
        type=ItemType.collectible,
        key='stick',
        name='Stick',
        emoji='<:stick:939923767344394240>',
        description='A stick. It\'s not very useful on it\'s own, but it can be used to craft other items. Although gainable from commands, you can manually craft these.',
        sell=100,
    )

    @fishing_pole.to_use
    async def use_fishing_pole(self, ctx: Context, _) -> None:
        await ctx.invoke(ctx.bot.get_command('fish'))

    axe = Item(
        type=ItemType.tool,
        key='axe',
        name='Axe',
        emoji='<:axe:937880907946283058>',
        description='Chop down trees using the `.chop` command to gain wood. You can sell wood, or save them for crafting!',
        price=10000,
        buyable=True,
    )

    @axe.to_use
    async def use_axe(self, ctx: Context, _) -> None:
        await ctx.invoke(ctx.bot.get_command('chop'))

    dirt = Item(
        type=ItemType.collectible,
        key='dirt',
        name='Dirt',
        emoji='<:dirt:939297925283086396>',
        description='A chunk of dirt that was dug up from the ground.',
        sell=10,
    )

    worm = Worm(
        key='worm',
        name='Worm',
        emoji='<:worm:938575708580634634>',
        description='The common worm. You can sell these or craft Fish Bait from these.',
        sell=100,
    )

    gummy_worm = Worm(
        key='gummy_worm',
        name='Gummy Worm',
        emoji='<:gummy_worm:939297088209055764>',
        description='A gummy worm - at least it\'s better than a normal worm.',
        sell=250,
    )

    earthworm = Worm(
        key='earthworm',
        name='Earthworm',
        emoji='<:earthworm:939297155997392926>',
        description='Quite literally an "earth" worm.',
        sell=500,
    )

    hook_worm = Worm(
        key='hook_worm',
        name='Hook Worm',
        emoji='<:hook_worm:939297533824467005>',
        description='hookworm',
        sell=1000,
        rarity=ItemRarity.uncommon,
    )

    poly_worm = Worm(
        key='poly_worm',
        name='Poly Worm',
        emoji='<:poly_worm:939297587213787157>',
        description='A very colorful worm',
        sell=1500,
        rarity=ItemRarity.rare,
    )

    ancient_relic = Item(
        type=ItemType.collectible,
        key='ancient_relic',
        name='Ancient Relic',
        emoji='<:ancient_relic:939304193934651402>',
        description='An ancient relic originally from an unknown cave. It\'s probably somewhere in the ground, I don\'t know.',
        sell=25000,
        rarity=ItemRarity.mythic,
    )

    shovel: Item[dict[Item, float]] = Item(
        type=ItemType.tool,
        key='shovel',
        name='Shovel',
        emoji='<:shovel:938575120157515786>',
        description='Dig up items from the ground using the `.dig` command. You can sell these items for profit.',
        price=10000,
        buyable=True,
        metadata={
            None: 1,
            dirt: 0.6,
            worm: 0.25,
            gummy_worm: 0.08,
            earthworm: 0.03,
            hook_worm: 0.0075,
            poly_worm: 0.0025,
            ancient_relic: 0.00005,  # 0.005%
        },
    )

    durable_shovel: Item[dict[Item, float]] = Item(
        type=ItemType.tool,
        key='durable_shovel',
        name='Durable Shovel',
        emoji='<:durable_shovel:939333623100874783>',
        description='A more durable version of a shovel. Tends to give more higher rarity items. This item cannot be directly bought - instead it must be crafted.',
        sell=30000,
        rarity=ItemRarity.rare,
        metadata={
            None: 1,
            dirt: 0.5,
            worm: 0.3,
            gummy_worm: 0.2,
            earthworm: 0.07,
            hook_worm: 0.02,
            poly_worm: 0.007,
            ancient_relic: 0.0001,  # 0.01%
        },
    )

    @shovel.to_use
    @durable_shovel.to_use
    async def use_shovel(self, ctx: Context, _) -> None:
        await ctx.invoke(ctx.bot.get_command('dig'))

    __shovels__: tuple[Item[dict[Item, float]], ...] = (
        durable_shovel,
        shovel,
    )

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
        rarity=ItemRarity.uncommon,
        sell=450,
    )

    lobster = Fish(
        key='lobster',
        name='Lobster',
        emoji='<:lobster:935288666283212830>',
        description='Lobsters are large crustaceans that are found in the ocean.',
        rarity=ItemRarity.uncommon,
        sell=575,
    )

    octopus = Fish(
        key='octopus',
        name='Octopus',
        plural='Octopuses',
        emoji='<:octopus:935292291143331900>',
        description='Octopuses have 3 hearts and 9 brains. And yes, that is the correct plural form of octopus.',
        rarity=ItemRarity.uncommon,
        sell=800,
    )

    dolphin = Fish(
        key='dolphin',
        name='Dolphin',
        emoji='<:dolphin:935294203364245601>',
        description='Dolphins are large aquatic mammals that are found in the ocean.',
        rarity=ItemRarity.rare,
        sell=1050,
    )

    shark = Fish(
        key='shark',
        name='Shark',
        emoji='<:shark:935301959949365249>',
        description='Sharks are large predatory fish that are found in the ocean.',
        rarity=ItemRarity.rare,
        sell=1250,
    )

    whale = Fish(
        key='whale',
        name='Whale',
        emoji='<:whale:935305582846566410>',
        description='Whales are huge mammals that swim deep in the ocean. How do you even manage to catch these?',
        rarity=ItemRarity.rare,
        sell=1760,
    )

    axolotl = Fish(
        key='axolotl',
        name='Axolotl',
        emoji='<:axolotl:935691745180667944>',
        description='The cool salamander',
        rarity=ItemRarity.epic,
        sell=3000,
    )

    vibe_fish = Fish(
        key='vibe_fish',
        name='Vibe Fish',
        plural='Vibe Fish',
        emoji='<a:vibe_fish:935293751604183060>',
        description='\uff56\uff49\uff42\uff45',  # "vibe" in full-width text
        rarity=ItemRarity.legendary,
        sell=6500,
    )

    wood = Wood(
        key='wood',
        name='Wood',
        plural='Wood',
        emoji='<:wood:937881094563463208>',
        description='The most abundant type of wood.',
        sell=30,
    )

    redwood = Wood(
        key='redwood',
        name='Redwood',
        plural='Redwood',
        emoji='<:redwood:937893043342815282>',
        description='Only found from Redwood trees whose lifespan is one of the longest.',
        sell=100,
    )

    blackwood = Wood(
        key='blackwood',
        name='Blackwood',
        plural='Blackwood',
        emoji='<:blackwood:937895087969566771>',
        description='A rare type of wood',
        rarity=ItemRarity.uncommon,
        sell=1000,
    )

    iron = Ore(
        key='iron',
        name='Iron',
        plural='Iron',
        emoji='<:iron:939598222408712202>',
        description='A common metal mined from the ground.',
        sell=60,
    )

    copper = Ore(
        key='copper',
        name='Copper',
        plural='Copper',
        emoji='<:copper:939598531432448080>',
        description='A soft metal with high thermal and electrial conductivity.',
        sell=200,
    )

    silver = Ore(
        key='silver',
        name='Silver',
        plural='Silver',
        emoji='<:silver:939599550027542578>',
        description='A shiny, lustrous metal with the highest thermal and electrical conductivity of any metal.',
        rarity=ItemRarity.uncommon,
        sell=400,
    )

    gold = Ore(
        key='gold',
        name='Gold',
        plural='Gold',
        emoji='<:gold:939600989474918471>',
        description='A bright, dense, and popular metal.',
        rarity=ItemRarity.rare,
        sell=900,
    )

    obsidian = Ore(
        key='obsidian',
        name='Obsidian',
        plural='Obsidian',
        emoji='<:obsidian:939604204346019950>',
        description='A volcanic, glassy mineral formed from the rapid cooling of felsic lava.',
        rarity=ItemRarity.rare,
        sell=1250,
    )

    emerald = Ore(
        key='emerald',
        name='Emerald',
        plural='Emerald',
        emoji='<:emerald:939603191115448370>',
        description='A valuable green gemstone.',
        rarity=ItemRarity.epic,
        sell=2000,
    )

    diamond = Ore(
        key='diamond',
        name='Diamond',
        plural='Diamond',
        emoji='<:diamond:939601998867746848>',
        description='A super-hard mineral known for being extremely expensive.',
        rarity=ItemRarity.legendary,
        sell=5000,
    )

    pickaxe: Item[dict[Item, float]] = Item(
        type=ItemType.tool,
        key='pickaxe',
        name='Pickaxe',
        emoji='<:pickaxe:939598952284692520>',
        description='Mine ores using the `.mine` command. You can sell these ores for profit, and use some in crafting.',
        price=10000,
        buyable=True,
        metadata={
            None: 1,
            iron: 0.5,
            copper: 0.17,
            silver: 0.075,
            gold: 0.015,
            obsidian: 0.005,
            emerald: 0.0015,
            diamond: 0.0003,
        },
    )

    durable_pickaxe = Item(
        type=ItemType.tool,
        key='durable_pickaxe',
        name='Durable Pickaxe',
        emoji='<:durable_pickaxe:939681326896930856>',
        description='A durable, re-enforced pickaxe. Able to find rare ores more commonly than a normal pickaxe. This item must be crafted.',
        rarity=ItemRarity.rare,
        sell=30000,
        metadata={
            None: 0.95,
            iron: 0.5,
            copper: 0.25,
            silver: 0.1,
            gold: 0.03,
            obsidian: 0.0075,
            emerald: 0.003,
            diamond: 0.00075,
        },
    )

    diamond_pickaxe = Item(
        type=ItemType.tool,
        key='diamond_pickaxe',
        name='Diamond Pickaxe',
        emoji='<:diamond_pickaxe:939683191785148476>',
        description='A pickaxe made of pure diamond. This pickaxe is better than both the normal and durable pickaxes. This item must be crafted.',
        rarity=ItemRarity.legendary,
        sell=200000,
        metadata={
            None: 0.9,
            iron: 0.5,
            copper: 0.3,
            silver: 0.15,
            gold: 0.05,
            obsidian: 0.015,
            emerald: 0.0075,
            diamond: 0.002,
        },
    )

    @pickaxe.to_use
    @durable_pickaxe.to_use
    @diamond_pickaxe.to_use
    async def use_pickaxe(self, ctx: Context, _) -> None:
        await ctx.invoke(ctx.bot.get_command('mine'))

    __pickaxes__: tuple[Item[dict[Item, float]]] = (
        diamond_pickaxe,
        durable_pickaxe,
        pickaxe,
    )

    common_crate = Crate(
        key='common_crate',
        name='Common Crate',
        emoji='<:crate:938163970248966165>',
        description='The most common type of crate.',
        price=200,
        sellable=False,
        metadata=CrateMetadata(
            minimum=200,
            maximum=600,
            items={
                banknote: (0.05, 1, 1),
                padlock: (0.5, 1, 1),
            },
        ),
    )

    uncommon_crate = Crate(
        key='uncommon_crate',
        name='Uncommon Crate',
        emoji='<:uncommon_crate:938165259301171310>',
        description='A slightly more common type of crate.',
        price=500,
        sellable=False,
        metadata=CrateMetadata(
            minimum=500,
            maximum=1500,
            items={
                banknote: (0.15, 1, 1),
                cheese: (0.5, 1, 2),
                lifesaver: (0.5, 1, 1),
                padlock: (0.75, 1, 2),
            },
        ),
        rarity=ItemRarity.uncommon,
    )

    @common_crate.to_use
    @uncommon_crate.to_use
    async def use_crate(self, ctx: Context, crate: Item[CrateMetadata], quantity: int) -> None:
        if quantity == 1:
            formatted = f'{crate.singular} {crate.name}'
        else:
            formatted = f'{quantity:,} {crate.plural}'

        original = await ctx.send(f'{crate.emoji} Opening {formatted}...', reference=ctx.message)

        metadata = crate.metadata
        profit = random.randint(metadata.minimum * quantity, metadata.maximum * quantity)

        async with ctx.db.acquire() as conn:
            record = await ctx.db.get_user_record(ctx.author.id)
            await record.add(wallet=profit, connection=conn)

            items = defaultdict(int)

            for _ in range(quantity):
                for item, (chance, lower, upper) in metadata.items.items():
                    if random.random() >= chance:
                        continue

                    amount = random.randint(lower, upper)

                    items[item] += amount
                    await record.inventory_manager.add_item(item, amount, connection=conn)
                    break

        await asyncio.sleep(random.uniform(1.5, 3.5))

        readable = f'{Emojis.coin} {profit:,}\n' + '\n'.join(
            f'{item.emoji} {item.name} x{quantity:,}' for item, quantity in items.items()
        )
        await original.edit(content=f'You opened {formatted} and received:\n{readable}')

    @classmethod
    def all(cls) -> Generator[Item, Any, Any]:
        """Lazily iterates through all items."""
        for attr in dir(cls):
            if isinstance(item := getattr(cls, attr), Item):
                yield item


ITEMS_INST = Items()

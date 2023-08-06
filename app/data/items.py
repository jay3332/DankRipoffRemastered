from __future__ import annotations

import asyncio
import datetime
import random
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from functools import partial
from textwrap import dedent
from typing import Any, Awaitable, Callable, Generator, Generic, NamedTuple, TYPE_CHECKING, TypeAlias, TypeVar

from discord.ext.commands import BadArgument
from discord.utils import format_dt

from app.data.pets import Pet, Pets
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
    crop = 7
    harvest = 8
    net = 9
    miscellaneous = 10


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


class CropMetadata(NamedTuple):
    time: int
    count: tuple[int, int]
    item: Item


class HarvestMetadata(NamedTuple):
    get_source_crop: Callable[[], Item[CropMetadata]]


class NetMetadata(NamedTuple):
    weights: dict[Pet, float]
    priority: int


@dataclass
class Item(Generic[T]):
    """Stores data about an item."""
    type: ItemType
    key: str
    name: str
    emoji: str
    description: str
    brief: str = None
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
    energy: int | None = None

    usage_callback: UsageCallback | None = None
    removal_callback: RemovalCallback | None = None

    def __post_init__(self) -> None:
        if not self.brief:
            self.brief = self.description.split('\n')[0]

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

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, self.__class__) and self.key == other.key

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

    def get_display_name(self, *, bold: bool = False, plural: bool = False) -> str:
        fmt = '{} **{}**' if bold else '{} {}'
        return fmt.format(self.emoji, self.plural if plural else self.name).strip()

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
Crate: Callable[..., Item[CrateMetadata]] = partial(Item, type=ItemType.crate, dispose=True, sellable=False)
Worm = partial(Item, type=ItemType.worm)
Ore = partial(Item, type=ItemType.ore)
Harvest = partial(Item, type=ItemType.harvest)
Net = partial(Item, type=ItemType.net)


def Crop(*, metadata: CropMetadata, **kwargs) -> Item[CropMetadata]:
    return Item(
        type=ItemType.crop,
        metadata=metadata,
        description=f'A crop that produces {metadata.item.emoji} {metadata.item.plural}.',
        buyable=True,
        sellable=False,
        **kwargs,
    )


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

    pistol = Item(
        type=ItemType.tool,
        key='pistol',
        name='Pistol',
        emoji='<:pistol:1134641571963338873>',
        brief='A quite deadly weapon that can be used to shoot and kill others.',
        description=(
            'A quite deadly weapon that can be used to shoot and kill others. We do not condone violence of any sort '
            '(especially with deadly weapons) in real life, but in this virtual economy system it is perfectly fine.\n\n'
            'Shoot others with the `shoot` command and steal their full wallet in the process. Owning a pistol also '
            'boosts profits from the `crime` command by **50%**.\n\n'
            'You can be protected against being shot by using a **lifesaver**. There is also a large chance that you can '
            'be caught by the police, pay a large fine, and even get yourself killed.'
        ),
        price=10_000,
        buyable=True,
    )

    alcohol = Item(
        type=ItemType.tool,
        key='alcohol',
        name='Alcohol',
        emoji='<:alcohol:1134641932178559027>',
        brief='Intoxicate yourself with alcohol for two hours!',
        description=(
            'Intoxicate yourself with alcohol! Drinking alcohol will make you drunk for two hours.\n\nWhile drunk, you will:\n'
            '- have a +25% coin multiplier,\n'
            '- have a +50% gambling multiplier,\n'
            '- have a +15% chance to successfully rob others,\n'
            '- have a +15% chance to successfully shoot others, **but:**\n'
            '- not be able to work,\n'
            '- are 20% more susceptible to being robbed, and\n'
            '- are 20% more susceptible to being shot.\n\n'
            'Additionally, when drinking alcohol, there is:\n'
            '- a small chance you will be caught by the police and pay a fine,\n'
            '- a small chance you will kill yourself of alcohol poisoning, and\n'
            '- a 6-hour cooldown from when you last drank alcohol for when you can drink again.'
        ),
        price=8_000,
        buyable=True,
    )

    ALCOHOL_USAGE_COOLDOWN = datetime.timedelta(hours=6)
    ALCOHOL_FINE_MESSAGES = (
        'You drink your alcohol in public alcohol-free zone and you are caught by the police. They force you to pay a fine of {}.',
        'You get a bit too woozy and break a few laws, you end up accumulating {} in fines.',
    )
    ALCOHOL_DEATH_MESSAGES = (
        'You drink a bit too much alcohol and die due to alcohol poisoning. Good going!',
    )

    @alcohol.to_use
    async def use_alcohol(self, ctx: Context, item: Item) -> None:
        record = await ctx.db.get_user_record(ctx.author.id)
        # enforce 6 hour cooldown
        if record.last_alcohol_usage and ctx.now - record.last_alcohol_usage <= self.ALCOHOL_USAGE_COOLDOWN:
            retry_at = record.last_alcohol_usage + self.ALCOHOL_USAGE_COOLDOWN
            raise ItemUsageError(
                "Calm down you drunkard, you're drinking too fast! "
                f"You can drink alcohol again {format_dt(retry_at, 'R')}."
            )

        message = await ctx.send(f'{item.emoji} Drinking the alcohol...')
        await asyncio.sleep(2)

        # pay a fine
        if random.random() < 0.1:
            fine = max(500, int(record.wallet * random.uniform(0.4, 1.0)))
            message = random.choice(self.ALCOHOL_FINE_MESSAGES).format(f'{Emojis.coin} **{fine}**')

            if record.wallet < 500:
                message += ' Since you\'re poor, they kill you instead and take your wallet.'
                async with ctx.db.acquire() as conn:
                    await record.make_dead(reason='not being able to afford fines', connection=conn)
                    await record.update(wallet=0)

                await ctx.send(f'\U0001f480 {message}')
                return

            await record.add(wallet=-fine)
            await ctx.send(f'\U0001f6a8 {message}')
            return

        # make dead
        if random.random() < 0.01:
            await record.make_dead(reason='alcohol poisoning')
            await ctx.send(f'\U0001f480 {random.choice(self.ALCOHOL_DEATH_MESSAGES)}')
            return

        await record.update(last_alcohol_usage=ctx.now)
        await ctx.maybe_edit(message, dedent(f'''
            You drink the {item.emoji} **Alcohol** and for the next two hours you are granted with:
            - a **+50%** coin multiplier,
            - a **+50%** gambling multiplier,
            - a **+15%** chance to successfully rob others, and
            - a **+15%** chance to successfully shoot others.
            
            However, for these two hours, you will also be:
            - unable to work,
            - **20%** more susceptible to being robbed, and
            - **20%** more susceptible to being shot.
        '''))

    @alcohol.to_remove
    async def remove_alcohol(self, ctx: Context, item: Item) -> None:
        record = await ctx.db.get_user_record(ctx.author.id)
        if record.alcohol_expiry is None:
            await ctx.send('You are not drunk (i.e. you don\'t have alcohol active).')
            return
        await record.update(last_alcohol_usage=None)
        await ctx.send(f'{item.emoji} Removed the effects of alcohol; you are no longer drunk.')

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
        record = await ctx.db.get_user_record(ctx.author.id)

        profit = random.randint(1000 * quantity, 3000 * quantity)
        additional = int(profit * record.prestige * 0.1)
        await record.add(max_bank=profit + additional)

        extra = ''
        if additional:
            extra = (
                f'\n{Emojis.Expansion.standalone} {Emojis.coin} +**{additional:,}** bank space because you are '
                f'{Emojis.get_prestige_emoji(record.prestige)} **Prestige {record.prestige}**.'
            )

        await message.edit(content=pluralize(
            f'{item.emoji} Your {quantity} banknote(s) expanded your bank space by {Emojis.coin} **{profit:,}**.{extra}'
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
            You now have a **{record.base_exp_multiplier:.02%}** base EXP multiplier.
        '''))

    cigarette = Item(
        type=ItemType.tool,
        key='cigarette',
        name='Cigarette',
        emoji='<:cigarette:1133107777359847585>',
        description=(
            'A standard cigarette. Smoking (using) these will do something beneficial in the future (WIP for now). '
            'These cannot be bought; you must craft this item.'
        ),
        rarity=ItemRarity.rare,
        sell=15000,
        dispose=True,
    )

    spinning_coin = Item(
        type=ItemType.collectible,
        key='spinning_coin',
        name='Spinning Coin',
        emoji='<a:spinning_coin:939937188836147240>',
        description='A coin but it spins automatically, cool isn\'t it?',
        price=500_000,
        rarity=ItemRarity.epic,
        buyable=True,
        sellable=False,
    )

    key = Item(
        type=ItemType.collectible,
        key='key',
        name='Key',
        emoji='\U0001f511',
        description='A key that has a small chance (25%) to open a padlock (when robbing). This can\'t be directrly bought; only received from commands.',
        rarity=ItemRarity.rare,
        sell=5_000,
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
        type=ItemType.miscellaneous,
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
        type=ItemType.miscellaneous,
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

    eel = Fish(
        key='eel',
        name='Eel',
        emoji='<:eel:1133878774706995262>',
        description='A long fish that is commonly found in the ocean. These are not obtainable from fishing.',
        rarity=ItemRarity.mythic,
        sell=35000,
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

    rare_crate = Crate(
        key='rare_crate',
        name='Rare Crate',
        emoji='<:rare_crate:938558029425700926>',
        description='A pretty rare crate.',
        price=2000,
        metadata=CrateMetadata(
            minimum=1500,
            maximum=3500,
            items={
                fishing_pole: (0.1, 1, 1),
                banknote: (0.15, 1, 2),
                cheese: (0.4, 1, 2),
                lifesaver: (0.5, 1, 2),
                padlock: (0.75, 1, 2),
            },
        ),
        rarity=ItemRarity.rare,
    )

    epic_crate = Crate(
        key='epic_crate',
        name='Epic Crate',
        emoji='<:epic_crate:938558716242976798>',
        description='A pretty epic crate.',
        price=6000,
        metadata=CrateMetadata(
            minimum=5000,
            maximum=12500,
            items={
                fishing_pole: (0.1, 1, 1),
                pickaxe: (0.1, 1, 1),
                shovel: (0.1, 1, 1),
                banknote: (0.2, 1, 3),
                fish_bait: (0.3, 5, 15),
                cheese: (0.4, 1, 3),
                lifesaver: (0.5, 1, 3),
                padlock: (0.75, 2, 3),
            },
        ),
        rarity=ItemRarity.epic,
    )

    legendary_crate = Crate(
        key='legendary_crate',
        name='Legendary Crate',
        emoji='<:legendary_crate:940383830177615952>',
        description='A pretty legendary crate.',
        price=25000,
        metadata=CrateMetadata(
            minimum=20000,
            maximum=50000,
            items={
                uncommon_crate: (0.01, 1, 1),
                common_crate: (0.01, 1, 1),
                fishing_pole: (0.1, 1, 1),
                pickaxe: (0.1, 1, 1),
                shovel: (0.1, 1, 1),
                axe: (0.1, 1, 1),
                banknote: (0.2, 1, 5),
                fish_bait: (0.3, 20, 50),
                cheese: (0.4, 2, 5),
                lifesaver: (0.5, 2, 4),
                padlock: (0.75, 2, 5),
            },
        ),
        rarity=ItemRarity.legendary,
    )

    mythic_crate = Crate(
        key='mythic_crate',
        name='Mythic Crate',
        emoji='<:mythic_crate:940385942080991302>',
        description='A pretty mythic crate.',
        price=60000,
        metadata=CrateMetadata(
            minimum=50000,
            maximum=150000,
            items={
                epic_crate: (0.002, 1, 1),
                rare_crate: (0.005, 1, 1),
                uncommon_crate: (0.01, 1, 1),
                common_crate: (0.01, 1, 2),
                fishing_pole: (0.1, 1, 2),
                pickaxe: (0.1, 1, 2),
                shovel: (0.1, 1, 2),
                axe: (0.1, 1, 2),
                banknote: (0.2, 2, 7),
                fish_bait: (0.3, 50, 100),
                cheese: (0.4, 2, 7),
                lifesaver: (0.5, 2, 6),
                padlock: (0.75, 3, 8),
            },
        ),
        rarity=ItemRarity.mythic,
    )

    net = Net(
        key='net',
        name='Net',
        emoji='<:net:1137070560753496104>',
        description='A net used to catch better pets using the `.hunt` command.',
        price=10000,
        buyable=True,
        metadata=NetMetadata(
            weights={
                None: 1,
                Pets.dog: 1,
                Pets.cat: 1,
                Pets.bird: 0.9,
                Pets.bee: 0.2,
            },
            priority=0,
        ),
    )

    @common_crate.to_use
    @uncommon_crate.to_use
    @rare_crate.to_use
    @epic_crate.to_use
    @legendary_crate.to_use
    @mythic_crate.to_use
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

    cup = Item(
        type=ItemType.tool,
        key='cup',
        name='Cup',
        emoji='<:cup:941091217993769031>',
        description='A cup that can hold liquid. Relatively cheap.',
        price=50,
        buyable=True
    )

    watering_can = Item(
        type=ItemType.tool,
        key='watering_can',
        name='Watering Can',
        emoji='<:watering_can:941088588068683808>',
        description='Use these to water your plants [crops], boosting their EXP.',
        price=1000,
        buyable=True,
    )

    glass_of_water = Item(
        type=ItemType.tool,
        key='glass_of_water',
        name='Glass of Water',
        plural='Glasses of Water',
        emoji='<:glass_of_water:941090007412785173>',
        description='Usually used for crafting, but can also be a refresher.',
        sell=1000,
    )

    tomato = Harvest(
        key='tomato',
        name='Tomato',
        plural='Tomatoes',
        emoji='<:tomato:940794702175801444>',
        description='A regular tomato, grown from the tomato crop.',
        sell=50,
    )

    tomato_crop = Crop(
        key='tomato_crop',
        name='Tomato Crop',
        emoji='<:tomato:940794702175801444>',
        price=1200,
        metadata=CropMetadata(
            time=600,
            count=(1, 3),
            item=tomato,
        ),
    )

    wheat = Harvest(
        key='wheat',
        name='Wheat',
        plural='Wheat',
        emoji='<:wheat:941089760317952020>',
        description='An ear of wheat, grown from the wheat crop.',
        sell=40,
    )

    wheat_crop = Crop(
        key='wheat_crop',
        name='Wheat Crop',
        emoji='<:wheat:941089760317952020>',
        price=1250,
        metadata=CropMetadata(
            time=600,
            count=(1, 2),
            item=wheat,
        ),
    )

    carrot = Harvest(
        key='carrot',
        name='Carrot',
        emoji='<:carrot:941096334365175839>',
        description='A carrot, grown from the carrot crop.',
        sell=75,
    )

    carrot_crop = Crop(
        key='carrot_crop',
        name='Carrot Crop',
        emoji='<:carrot:941096334365175839>',
        price=2000,
        metadata=CropMetadata(
            time=800,
            count=(1, 2),
            item=carrot,
        ),
    )

    corn = Harvest(
        key='corn',
        name='Corn',
        plural='Corn',
        emoji='<:corn:941097271544643594>',
        description='An ear of corn, grown from the corn crop.',
        sell=75,
    )

    corn_crop = Crop(
        key='corn_crop',
        name='Corn Crop',
        emoji='<:corn:941097271544643594>',
        price=2200,
        metadata=CropMetadata(
            time=800,
            count=(1, 1),
            item=corn,
        ),
    )

    lettuce = Harvest(
        key='lettuce',
        name='Lettuce',
        plural='Lettuce',
        emoji='<:lettuce:941136607594041344>',
        description='A head of lettuce, grown from the lettuce crop.',
        sell=80,
    )

    lettuce_crop = Crop(
        key='lettuce_crop',
        name='Lettuce Crop',
        emoji='<:lettuce:941136607594041344>',
        price=2400,
        metadata=CropMetadata(
            time=1200,
            count=(1, 2),
            item=lettuce,
        ),
    )

    potato = Harvest(
        key='potato',
        name='Potato',
        plural='Potatoes',
        emoji='<:potato:941139578226626682>',
        description='A potato, grown from the potato crop.',
        sell=110,
    )

    potato_crop = Crop(
        key='potato_crop',
        name='Potato Crop',
        emoji='<:potato:941139578226626682>',
        price=2800,
        metadata=CropMetadata(
            time=1500,
            count=(1, 2),
            item=potato,
        ),
    )

    tobacco = Harvest(
        key='tobacco',
        name='Tobacco',
        plural='Tobacco',
        emoji='<:tobacco:941445316765421688>',
        description='A piece of tobacco, grown from the tobacco crop.',
        sell=125,
    )

    tobacco_crop = Crop(
        key='tobacco_crop',
        name='Tobacco Crop',
        emoji='<:tobacco:941445316765421688>',
        price=3600,
        metadata=CropMetadata(
            time=1500,
            count=(1, 2),
            item=tobacco,
        ),
    )
    
    cotton_ball = Harvest(
        key='cotton_ball',
        name='Cotton Ball',
        emoji='<:cottonball:1132871115014950964>',
        description='A ball of cotton, grown from the cotton crop.',
        sell=150,
        metadata=HarvestMetadata(lambda: Items.cotton_crop),
    )

    cotton_crop = Crop(
        key='cotton_crop',
        name='Cotton Crop',
        emoji='<:cotton:1132867001057030184>',
        price=4500,
        metadata=CropMetadata(
            time=1800,
            count=(1, 2),
            item=cotton_ball,
        ),
    )

    flour = Item(
        type=ItemType.miscellaneous,
        key='flour',
        name='Flour',
        plural='Flour',
        emoji='<:flour:941087131038797834>',
        description='A bag of flour, used to make [craft] bakery products.',
        sell=100,
    )

    bread = Item(
        type=ItemType.miscellaneous,
        key='loaf_of_bread',
        name='Loaf of Bread',
        plural='Loaves of Bread',
        emoji='<:loaf_of_bread:941087632308457483>',
        description='A normal loaf of wheat bread.',
        sell=500,
        rarity=ItemRarity.uncommon,
        energy=5,
    )

    sheet_of_paper = Item(
        type=ItemType.miscellaneous,
        key='sheet_of_paper',
        name='Sheet of Paper',
        plural='Sheets of Paper',
        emoji='<:paper:1133127585258291240>',
        description='A sheet of paper.',
        sell=10000,
        rarity=ItemRarity.rare,
    )

    nineteen_dollar_fortnite_card = Item(
        type=ItemType.collectible,
        key='nineteen_dollar_fortnite_card',
        name='19 Dollar Fortnite Card',
        emoji='<a:19dollar:1133500138959163442>',
        description=(
            'Okay, 19 dollar Fortnite card, who wants it? And yes, I\'m giving it away. Remember; share, share share. '
            'And trolls, don\'t get blocked!'
        ),
        sell=50000,
        rarity=ItemRarity.mythic,
    )

    @classmethod
    def all(cls) -> Generator[Item, Any, Any]:
        """Lazily iterates through all items."""
        for attr in dir(cls):
            if isinstance(item := getattr(cls, attr), Item):
                yield item


ITEMS_INST = Items()

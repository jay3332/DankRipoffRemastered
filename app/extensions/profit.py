from __future__ import annotations

import asyncio
import random
from textwrap import dedent
from typing import NamedTuple, TYPE_CHECKING

import discord

from app.core import Cog, Context, EDIT, REPLY, command, simple_cooldown, user_max_concurrency
from app.data.items import Item, Items
from app.util.common import insert_random_u200b
from app.util.converters import Investment
from app.util.views import UserView
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


class SearchArea(NamedTuple):
    minimum: int
    maximum: int
    success_chance: float = 1
    death_chance_if_fail: float = 0
    success_responses: list[str] = []  # We can use a list literal here as these are defined as constants and will never be appended to.
    failure_responses: list[str] = []
    death_responses: list[str] = []
    items: dict[Item, float] = {}  # Similar situation with list literals


class SearchButton(discord.ui.Button['SearchView']):
    def __init__(self, name: str) -> None:
        super().__init__(label=name, style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        for button in self.view.children:
            if not isinstance(button, discord.ui.Button):
                continue

            button.style = discord.ButtonStyle.primary if button.label == self.label else discord.ButtonStyle.secondary
            button.disabled = True

        cog = self.view.ctx.cog
        assert isinstance(cog, Profit)

        self.view.choice = self.label, cog.SEARCH_AREAS[self.label]
        await interaction.response.edit_message(view=self.view)
        self.view.stop()


class SearchView(UserView):
    def __init__(self, ctx: Context, choices: list[str]) -> None:
        super().__init__(ctx.author, timeout=30)
        self.ctx: Context = ctx

        for choice in choices:
            self.add_item(SearchButton(choice))

        self.choice: tuple[str, SearchArea] | None = None

    async def on_timeout(self) -> None:
        await self.ctx.send('Timed out.', reference=self.ctx.message)


class Profit(Cog):
    """Commands you use to grind for profit."""

    BEG_INITIAL_MESSAGES = (
        "Alright, begging...",
        "Hold on, let me just beg *for you*...",
        "Begging...",
    )

    BEG_PEOPLE = (
        "jay3332",
        "your mother",
        "your father",
        "your left arm",
        "your right arm",
        "your left leg",
        "your right leg",
        "a worm",
        "a bird",
        "a homeless guy",
        "the president of the United States",
        "Joe Biden",
        "Donald Trump",
        "George Washington",
        "Barack Obama",
        "John Cena",
        "The Rock",
        "a rock",
        "Aagames",
        "an apple",
        "Tim Cook",
        "Steve Jobs",
        "Bill Gates",
        "Jeff Bezos",
        "Elon Musk",
        "a robot",
        "a cat",
        "a dog",
        "your nose",
        "me",
        "Jason Citron",
        'the popular video game "Among Us"',
    )

    BEG_FAIL_MESSAGES = (
        "lol! {} didn't give you anything because they didn't feel like it.",
        "funny, {} told you to get a job.",
        "ouch, {} simply denied your request.",
        "{} does not give to homeless people. Kinda rude wouldn't you say?",
    )

    BEG_SUCCESS_MESSAGES = (
        "Cool, {0} gave you {1}.",
        "{0} handed you {1} without hesitation.",
        "Nice stuff, you received {1} from {0}.",
        "{0} scooped up {1} from the toilet and handed it to you.",
        "{0} made {1} magically appear into your possesion.",
        "{0} vomitted out {1} and gave it to you.",
    )

    BEG_ITEMS = {
        Items.banknote: 0.05,
    }

    @staticmethod
    def _capitalize_first(s: str, /) -> str:
        if not len(s):
            return s

        return s[0].upper() + s[1:]

    # noinspection PyTypeChecker
    @command(aliases={"plead"})
    @simple_cooldown(1, 15)
    @user_max_concurrency(1)
    async def beg(self, ctx: Context):
        """Beg for coins. There is a chance that you can get nothing, and a small chance that you can obtain some items"""
        yield f"{Emojis.loading} {random.choice(self.BEG_INITIAL_MESSAGES)}", REPLY
        person = random.choice(self.BEG_PEOPLE)

        embed = discord.Embed(timestamp=ctx.now)
        embed.set_author(name=f"Beg: {ctx.author}", icon_url=ctx.author.avatar)

        await asyncio.sleep(random.uniform(2, 4))

        record = await ctx.db.get_user_record(ctx.author.id)
        await record.add_random_exp(4, 7)
        await record.add_random_bank_space(10, 15, chance=0.45)

        if random.random() < 0.4:
            embed.colour = Colors.error
            embed.description = self._capitalize_first(random.choice(self.BEG_FAIL_MESSAGES).format(f'**{person}**'))

            yield '', embed, EDIT
            return

        async with ctx.db.acquire() as conn:
            profit = await record.add_coins(random.randint(150, 450), connection=conn)
            message = f'{Emojis.coin} **{profit:,}**'

            for item, chance in self.BEG_ITEMS.items():
                if random.random() < chance:
                    message += f' and {item.get_sentence_chunk(1)}'
                    await record.inventory_manager.add_item(item, 1, connection=conn)
                    break

        embed.colour = Colors.success
        embed.description = self._capitalize_first(
            random.choice(self.BEG_SUCCESS_MESSAGES).format(person, message)
        )

        yield '', embed, EDIT
        return

    @command(aliases={"investment", "iv", "in"})
    @simple_cooldown(1, 20)
    @user_max_concurrency(1)
    async def invest(self, ctx: Context, *, amount: Investment()):
        """Invest your coins and potentially get more money. There is a chance that you could fail and lose your investment, however."""
        record = await ctx.db.get_user_record(ctx.author.id)
        await record.add_random_exp(4, 7)
        await record.add_random_bank_space(10, 15, chance=0.45)
        await record.add(wallet=-amount)

        def make_embed(c: int = Colors.primary) -> discord.Embed:
            e = discord.Embed(timestamp=ctx.now, color=c)
            # noinspection PyTypeChecker
            e.set_author(name=f"{ctx.author.name}'s Investment", icon_url=ctx.author.avatar)
            return e

        multiplier = 0
        yield f'{Emojis.loading} Please wait...', REPLY

        for _ in range(5):
            if random.random() > 0.15:
                multiplier += random.uniform(.14, .27)

                embed = make_embed()
                embed.description = f'{Emojis.loading} Investing...'

                embed.add_field(name="Earnings", value=dedent(f"""
                    {multiplier:,.1%} of initial value
                    \u2937 {Emojis.coin} +{round(amount * multiplier):,}
                """), inline=False)

                embed.add_field(name="Total Return", value=f"{Emojis.coin} {amount * (1 + multiplier):,.0f}")

                yield embed, EDIT

            else:
                embed = make_embed(Colors.error)
                embed.description = "You failed to invest properly. Lol."

                yield "", embed, EDIT
                return

            await asyncio.sleep(2)

        profit = await record.add_coins(round(amount * (1 + multiplier)))

        embed = make_embed(Colors.success)
        embed.description = 'Success! Your investment succeeded.'

        embed.add_field(name="Earnings", value=dedent(f"""
            {multiplier:,.1%} of initial value
            \u2937 {Emojis.coin} +{amount * multiplier:,.0f}
        """), inline=False)

        embed.add_field(name="Total Return", value=f"{Emojis.coin} {profit:,}")
        yield "", embed, EDIT

    SEARCH_AREAS = {
        'bathroom': SearchArea(
            minimum=200,
            maximum=320,
            success_chance=0.8,
            death_chance_if_fail=0.1,
            success_responses=[
                'You found {} in the toilet. Was it really worth it though?',
                'You found {} in the bathtub.',
                'You dug through the unflushed toilet and found {}. Disgusting you.'
            ],
            failure_responses=[
                'You put your hand deep in the toilet only to come out with no coins.',
                'You simply could not find anything in the bathroom.',
            ],
            death_responses=[
                'You got stuck in the toilet and drowned yourself - wtf?',
                'You drown in the bathtub, nice job.',
            ],
        ),
        'trash can': SearchArea(
            minimum=50,
            maximum=550,
            success_chance=0.65,
            success_responses=[
                'You now really stink, but at least you found {} in the trash can.',
                'You simply find {} in the trash can.'
            ],
            failure_responses=[
                'You got stuck in the trash can, lmao',
                'Not only do you stink now, but you found absolutely nothing in the trash can.',
            ],
        ),
        'car': SearchArea(
            minimum=100,
            maximum=500,
            success_chance=0.7,
            death_chance_if_fail=0.15,
            success_responses=[
                'You find {} inside of your car.',
                'You find {} on top of the passenger seat.',
            ],
            failure_responses=[
                'You try going to your car to find some coins, but then it hits you. You don\'t own a car! Silly you.',
                'You could not find anything inside of your __brand new__ car.',
            ],
            death_responses=[
                'You look under your car, but you left it in driving mode. Your car runs you over.',
                'You were held at gunpoint for driving a hijacked car. Reluctant to comply, you were shot and killed by the police.',
            ],
            items={
                Items.banknote: 0.03,
            },
        ),
        'bank': SearchArea(
            minimum=200,
            maximum=800,
            success_chance=0.45,
            death_chance_if_fail=0.4,
            success_responses=[
                'You find {} at the bank.',
                'You sneak into the bank at 3 in the morning. You find {} and get out without a trace.',
            ],
            failure_responses=[
                'Lol, the bank was closed.',
                'You did not find anything at the bank.',
            ],
            death_responses=[
                'You were caught breaking into the bank. You were shot and killed by the police.',
            ],
            items={
                Items.banknote: 0.15,
            },
        ),
        'house': SearchArea(  # credit: Clammerz
            minimum=200,
            maximum=700,
            success_chance=0.6,
            death_chance_if_fail=0.2,
            success_responses=[
                'You stole {} from the dresser.',
                'You found {} in the spare room.',
                'You stole {} from a childs piggy bank.',
                'Unexpectedly, their cat helped you find {}.',
                'You sneak into the house at 2 in the morning. You find {} and get out without causing a ruckus.',
            ],
            failure_responses=[
                'Lol, the doors and windows were locked.',
                'Their dog started barking and you swiftly ran away.',
                'The police caught you breaking in, but you got away just in time.',
                'You dropped a cereal bowl causing the owner to wake up, you got away before they saw you.',
            ],
            death_responses=[
                'You were caught breaking into someones house (in America). You were shot and killed by the owner.',
                'The police saw you breaking in; you weren\'t fast enough and ended up getting shot by the police.',
                'While scrounging for money, the owner knocked you out and tortured you til you met your demise.',
                'You punctured an artery on the broken window and bled out soon after.',
            ],
            items={
                Items.banknote: 0.06,
            },
        ),
        'shoe': SearchArea(
            minimum=300,
            maximum=700,
            success_chance=0.44,
            death_chance_if_fail=0.03,
            success_responses=[
                'Your shoe had {} in it???',
                '{} was hiding in your shoe.',
                'There was {} in your shoe, must\'ve been uncomfortable.',
            ],
            failure_responses=[
                'There was nothing in your shoe.',
                'Why would there be money in your shoe?',
                'Your shoe isn\'t your wallet.',
                'Maybe ask your sock, it might have some coins.',
                'What were you expecting? It\'s your shoe not a bank.',
            ],
            death_responses=[
                'The shoe literally ate you.',
            ],
        ),
        'sock': SearchArea(
            minimum=200,
            maximum=500,
            success_chance=0.45,
            death_chance_if_fail=0.1,
            success_responses=[
                'Okay now, who put {} coins inside of your sock?',
                'Your sock was holding {} coins hostage.',
                'There was {} in your sock, how did you wear this thing..?',
                'You found {} inside of your sock. Yeah, I know - who *doesn\'t* put coins inside of their socks?',
            ],
            failure_responses=[
                'Sadly, your sock had no coins to offer.',
                'I wonder why there are no coins in a sock.',
                'Maybe ask your shoe, it might have some coins.',
                'Who puts coins in their socks?',
            ],
            death_responses=[
                'The sock captured you and fed you to the shoe.',
            ],
        ),
    }

    @command(aliases={'se', 'sch'})
    @simple_cooldown(1, 20)
    @user_max_concurrency(1)
    async def search(self, ctx: Context):
        """Search for coins."""
        view = SearchView(ctx, random.sample(list(self.SEARCH_AREAS), 3))
        yield 'Where would you like to search?', view, REPLY

        await view.wait()
        if not view.choice:
            return

        record = await ctx.db.get_user_record(ctx.author.id)
        await record.add_random_exp(10, 16)
        await record.add_random_bank_space(18, 24, chance=0.6)

        name, choice = view.choice
        embed = discord.Embed(timestamp=ctx.now)
        embed.set_author(name=f'Search: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_footer(text=f'Search area: {name}')

        if random.random() > choice.success_chance:
            embed.colour = Colors.error

            if random.random() < choice.death_chance_if_fail:
                cause = random.choice(choice.death_responses)
                await record.make_dead(reason=f'While searching for coins, {cause}')

                embed.add_field(name='You died!', value=cause)

                yield embed, REPLY
                return

            message = random.choice(choice.failure_responses)
            embed.add_field(name='You found nothing!', value=message)

            yield embed, REPLY
            return

        async with ctx.db.acquire() as conn:
            profit = await record.add_coins(random.randint(choice.minimum, choice.maximum), connection=conn)
            message = f'{Emojis.coin} **{profit:,}**'

            for item, chance in choice.items.items():
                if random.random() < chance:
                    message += f' and {item.get_sentence_chunk(1)}'
                    await record.inventory_manager.add_item(item, 1, connection=conn)
                    break

        embed.colour = Colors.success
        embed.add_field(name='Profit!', value=random.choice(choice.success_responses).format(message))

        yield embed, REPLY

    FISH_CHANCES = {
        None: 1,
        Items.fish: 0.4,
        Items.sardine: 0.25,
        Items.angel_fish: 0.175,
        Items.blowfish: 0.125,
        Items.crab: 0.075,
        Items.lobster: 0.04,
        Items.octopus: 0.02,
        Items.dolphin: 0.0075,
        Items.shark: 0.004,
        Items.whale: 0.0015,
        Items.axolotl: 0.0005,
        Items.vibe_fish: 0.00025,
    }

    FISH_CHANCES_WITH_BAIT = {
        None: 1,
        Items.fish: 0.4,
        Items.sardine: 0.25,
        Items.angel_fish: 0.2,
        Items.blowfish: 0.2,
        Items.crab: 0.125,
        Items.lobster: 0.055,
        Items.octopus: 0.03,
        Items.dolphin: 0.015,
        Items.shark: 0.0085,
        Items.whale: 0.0035,
        Items.axolotl: 0.0015,
        Items.vibe_fish: 0.00075,
    }

    RARE_FISH = {
        Items.octopus,
        Items.dolphin,
        Items.shark,
        Items.whale,
        Items.axolotl,
        Items.vibe_fish,
    }

    FISHING_PROMPTS = (
        "that's a big fish",
        "what a heavy one",
        "this must be something special",
        "mom, get the camera!",
        "what must this be?",
        "fish fish fish",
        "my fishing pole is about to break",
    )

    @command(aliases={'f', 'cast', 'fishing', 'fishingpole'})
    @simple_cooldown(1, 25)
    @user_max_concurrency(1)
    async def fish(self, ctx: Context):
        """Using your fishing pole to fish for fish and sell them for profit!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()

        if not inventory.cached.quantity_of('fishing_pole'):
            yield f'You need {Items.fishing_pole.get_sentence_chunk(1)} to fish.', REPLY
            return

        if inventory.cached.quantity_of('fish_bait'):
            mapping = self.FISH_CHANCES_WITH_BAIT
            await inventory.add_item('fish_bait', -1)
        else:
            mapping = self.FISH_CHANCES

        fish = random.choices(list(mapping), weights=list(mapping.values()), k=5)
        fish = {item: fish.count(item) for item in set(fish) if item is not None}

        yield f'{Emojis.loading} Casting your fishing pole...', REPLY
        await asyncio.sleep(random.uniform(2., 4.))

        if not len(fish):
            yield 'You caught absolutely nothing. Lmao.', EDIT
            return

        if any(f in self.RARE_FISH for f in fish):
            message = random.choice(self.FISHING_PROMPTS)

            yield (
                f'Looks like one of the fish you caught was pretty heavy! Type `{insert_random_u200b(message)}` to wind up your fishing pole before it breaks!',
                EDIT,
            )

            try:
                response = await ctx.bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=10)
                initial = "You failed to wind up your fishing pole"

            except asyncio.TimeoutError:
                response = ctx.message  # guaranteed that this wont equal the message
                initial = "You couldn't wind up your fishing pole in time"

            if response.content.lower() != message:
                await inventory.add_item('fishing_pole', -1)

                if random.random() < 0.15:
                    await record.make_dead(reason='a fish biting your head off')

                    yield f'{initial}, and the fish jumped out of the water and bit your head off. You died, and also lost your fishing pole.', REPLY
                    return

                yield f'{initial}, and your fishing pole snapped in half. Nice one.', REPLY
                return

        async with ctx.db.acquire() as conn:
            for item, count in fish.items():
                await inventory.add_item(item, count, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)

        embed.add_field(name='You caught:', value='\n'.join(f'{item.get_display_name(bold=True)} x{count}' for item, count in fish.items()))
        embed.set_author(name=f'Fishing: {ctx.author}', icon_url=ctx.author.avatar.url)

        yield '', embed, EDIT


def setup(bot: Bot) -> None:
    bot.add_cog(Profit(bot))

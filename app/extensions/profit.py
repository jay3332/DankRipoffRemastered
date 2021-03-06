from __future__ import annotations

import asyncio
import datetime
import random
from collections import deque
from datetime import timedelta
from html import unescape
from textwrap import dedent
from typing import Any, Generic, Literal, NamedTuple, TypeVar

import discord
import requests

from app.core import (
    BAD_ARGUMENT,
    Cog,
    Context,
    EDIT,
    REPLY,
    command,
    database_cooldown,
    lock_transactions,
    simple_cooldown,
    user_max_concurrency
)
from app.core.helpers import cooldown_message
from app.data.items import Item, Items
from app.data.skills import RobberyTrainingButton
from app.util.common import humanize_list, insert_random_u200b
from app.util.converters import CaseInsensitiveMemberConverter, Investment
from app.util.structures import LockWithReason
from app.util.views import AnyUser, UserView
from config import Colors, Emojis


class SearchArea(NamedTuple):
    minimum: int
    maximum: int
    success_chance: float = 1
    death_chance_if_fail: float = 0
    success_responses: list[str] = []  # We can use a list literal here as these are defined as constants and will never be appended to.
    failure_responses: list[str] = []
    death_responses: list[str] = []
    items: dict[Item, float] = {}  # Similar situation with list literals


class CrimeData(NamedTuple):
    minimum: int
    maximum: int
    image: str = ''

    success_chance: float = 1
    death_chance_if_fail: float = 0
    success_responses: list[str] = []
    failure_responses: list[str] = []
    death_responses: list[str] = []

    item_chance: float = 0
    item_count: tuple[int, int] = 1, 1
    items: dict[Item, float] = {}


class SearchButton(discord.ui.Button['SearchView']):
    def __init__(self, name: str) -> None:
        super().__init__(label=name, style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        for button in self.view.children:
            if not isinstance(button, discord.ui.Button):
                continue

            button.style = discord.ButtonStyle.primary if button.label == self.label else discord.ButtonStyle.secondary
            button.disabled = True

        self.view.choice = self.label, self.view.mapping[self.label]
        await interaction.response.edit_message(view=self.view)
        self.view.stop()


T = TypeVar('T', SearchArea, CrimeData)


class SearchView(UserView, Generic[T]):
    def __init__(self, ctx: Context, choices: list[str], mapping: dict[str, T]) -> None:
        super().__init__(ctx.author, timeout=30)
        self.ctx: Context = ctx

        for choice in choices:
            self.add_item(SearchButton(choice))

        self.choice: tuple[str, T] | None = None
        self.mapping: dict[str, T] = mapping

    async def on_timeout(self) -> None:
        await self.ctx.send('Timed out.', reference=self.ctx.message)


class RobData(NamedTuple):
    timestamp: datetime.datetime
    robbed_by: AnyUser
    victim: AnyUser
    amount: int


class TriviaQuestion(NamedTuple):
    category: str
    type: Literal['multiple', 'boolean']
    difficulty: Literal['easy', 'medium', 'hard']
    question: str
    correct_answer: str
    incorrect_answers: list[str]

    @property
    def answers(self) -> list[str]:
        if self.type == 'multiple':
            entities = [self.correct_answer] + self.incorrect_answers
            random.shuffle(entities)
            return entities

        return ['True', 'False']

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> TriviaQuestion:
        data['question'] = unescape(data['question'])
        data['correct_answer'] = unescape(data['correct_answer'])
        data['incorrect_answers'] = [unescape(answer) for answer in data['incorrect_answers']]

        return cls(**data)


class Profit(Cog):
    """Commands you use to grind for profit."""

    def __setup__(self) -> None:
        self._recent_robs: dict[int, RobData] = {}
        self._trivia_questions: deque[TriviaQuestion] = deque(maxlen=50)
        self._trivia_questions_fetch_lock: asyncio.Lock = asyncio.Lock()

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
        Items.stick: 0.1,
        Items.padlock: 0.1,
        Items.cheese: 0.05,
        Items.banknote: 0.05,
        Items.common_crate: 0.03,
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

        multiplier = 1
        item_chance = 0.06

        skills = await record.skill_manager.wait()
        if begging_skill := skills.get_skill('begging'):
            multiplier += begging_skill.points * 0.02
            item_chance += begging_skill.points * 0.005

        async with ctx.db.acquire() as conn:
            profit = await record.add_coins(random.randint(150, 450) * multiplier, connection=conn)
            message = f'{Emojis.coin} **{profit:,}**'

            if random.random() < item_chance:
                item = random.choices(list(self.BEG_ITEMS), list(self.BEG_ITEMS.values()))[0]

                message += f' and {item.get_sentence_chunk(1)}'
                await record.inventory_manager.add_item(item, 1, connection=conn)

        embed.colour = Colors.success
        embed.description = self._capitalize_first(
            random.choice(self.BEG_SUCCESS_MESSAGES).format(person, message)
        )

        yield '', embed, EDIT
        return

    @command(aliases={"investment", "iv", "in"})
    @simple_cooldown(1, 20)
    @user_max_concurrency(1)
    @lock_transactions
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
            items={
                Items.stick: 0.04,
                Items.cheese: 0.02,
            }
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
                Items.padlock: 0.06,
                Items.banknote: 0.03,
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

    @command(aliases={'se', 'sch', 'scout'})
    @simple_cooldown(1, 20)
    @user_max_concurrency(1)
    async def search(self, ctx: Context):
        """Search for coins."""
        view: SearchView[SearchArea] = SearchView(ctx, random.sample(list(self.SEARCH_AREAS), 3), self.SEARCH_AREAS)
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

    CRIMES = {
        'shoplift': CrimeData(
            minimum=100,
            maximum=300,
            image='https://cdn.discordapp.com/attachments/935327142332465222/942470170562138202/Untitled352_20220213120854.png',
            success_chance=0.4,
            death_chance_if_fail=0.3,
            success_responses=[
                'You stole {} from the shop!',
                'You were caught stealing {} from the shop, but you got away just in time.',
            ],
            failure_responses=[
                'The store was closed, maybe try shoplifting when the store is open next time.',
                'You were caught stealing from the shop, but you got away just in time while having to drop your items.',
            ],
            death_responses=[
                'You were caught stealing from the shop and you were reluctant to comply with the police; so they shot you instead.',
                'You slipped on a banana peel while trying to run out of the shop and fell head first into concrete. You died.',
            ],
            item_chance=0.75,
            item_count=(1, 2),
            items={
                Items.cup: 1.1,
                Items.tomato: 1,
                Items.corn: 1,
                Items.bread: 0.8,
                Items.padlock: 0.7,
                Items.cheese: 0.6,
                Items.lifesaver: 0.5,
                Items.banknote: 0.15,
                Items.fishing_pole: 0.1,
            },
        ),
        'pickpocket': CrimeData(
            minimum=400,
            maximum=900,
            image='https://cdn.discordapp.com/attachments/935327142332465222/942470170348244992/Untitled352_20220213121146.png',
            success_chance=0.35,
            death_chance_if_fail=0.45,
            success_responses=[
                'You stealthily take {} out of the victim\'s pocket.',
                'You distract the victim and steal {} from their pocket.',
            ],
            failure_responses=[
                'The victim had nothing in their pocket, lol.',
                'You were caught stealing from the victim, but you got away just in time.',
            ],
            death_responses=[
                'The victim caught you trying to steal from them and shot you in the head in self-defense.',
                'You pickpocket a mine which explodes in your hand, killing you.'
            ],
            item_chance=0.4,
            items={
                Items.tobacco: 0.5,
                Items.padlock: 0.3,
                Items.key: 0.3,
                Items.banknote: 0.1,
            },
        ),
        'rob': CrimeData(
            minimum=500,
            maximum=800,
            image='https://cdn.discordapp.com/attachments/935327142332465222/942470172286001203/Untitled347_20220212181014.png',
            success_chance=0.4,
            death_chance_if_fail=0.3,
            success_responses=[
                'You robbed an old lady on the street for {}.',
                "You steal someone's paycheck which contained {}.",
            ],
            failure_responses=[
                'Maybe don\'t try robbing a bank with a banana next time.',
                'You tried robbing someone with a nerf gun, lol.',
            ],
            death_responses=[
                'You were caught robbing a bank and got shot by the police.',
                'You were beaten to death for trying to steal from the elderly.',
            ],
            item_chance=0.42,
            items={
                Items.tobacco: 0.7,
                Items.key: 0.2,
                Items.banknote: 0.2,
            },
        ),
        'arson': CrimeData(
            minimum=500,
            maximum=800,
            image='https://cdn.discordapp.com/attachments/935327142332465222/942470172529291376/Untitled347_20220212180424.png',
            success_chance=0.55,
            death_chance_if_fail=0.5,
            success_responses=[
                'You burn down the house and get paid a bounty of {}.',
                'You watch the building burn in flames and somehow receive {}.',
            ],
            failure_responses=[
                'You burned down a house, now what?',
                'You tried to burn down a fireproof building.',
            ],
            death_responses=[
                'You tried to burn down a police station and ended up getting shot by the police.',
                'You were caught in the fire you created and died.'
            ],
            item_chance=0.35,
            items={
                Items.fish: 0.8,
                Items.padlock: 0.2,
                Items.banknote: 0.1,
                Items.fishing_pole: 0.1,
                Items.key: 0.1,
            },
        ),
    }

    @command(aliases={'ci', 'cri', 'felony', 'criminal'})
    @simple_cooldown(1, 25)
    @user_max_concurrency(1)
    async def crime(self, ctx: Context):
        """Commit a crime and hope for profit."""
        view: SearchView[CrimeData] = SearchView(ctx, random.sample(list(self.CRIMES), 3), self.CRIMES)
        yield 'Which crime would you like to commit?', view, REPLY

        await view.wait()
        if not view.choice:
            return

        record = await ctx.db.get_user_record(ctx.author.id)
        await record.add_random_exp(10, 16)
        await record.add_random_bank_space(18, 24, chance=0.6)

        name, choice = view.choice
        embed = discord.Embed(timestamp=ctx.now)
        embed.set_author(name=f'Crime: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_footer(text=f'Crime committed: {name}')
        embed.set_thumbnail(url=choice.image)

        if random.random() > choice.success_chance:
            embed.colour = Colors.error

            if random.random() < choice.death_chance_if_fail:
                cause = random.choice(choice.death_responses)
                await record.make_dead(reason=f'While committing a crime, {cause}')

                embed.add_field(name='You died!', value=cause)

                yield embed, REPLY
                return

            message = random.choice(choice.failure_responses)
            embed.add_field(name='You got nothing!', value=message)

            yield embed, REPLY
            return

        async with ctx.db.acquire() as conn:
            profit = await record.add_coins(random.randint(choice.minimum, choice.maximum), connection=conn)
            message = [f'{Emojis.coin} **{profit:,}**']

            if random.random() < choice.item_chance:
                items = random.choices(list(choice.items), list(choice.items.values()), k=random.randint(*choice.item_count))
                message.extend(item.get_sentence_chunk(1) for item in items)

        embed.colour = Colors.success
        embed.add_field(name='Profit!', value=random.choice(choice.success_responses).format(humanize_list(message)))

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
        """Use your fishing pole to fish for fish and sell them for profit!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()

        if not inventory.cached.quantity_of('fishing_pole'):
            yield f'You need {Items.fishing_pole.get_sentence_chunk(1)} to fish.', BAD_ARGUMENT
            return

        if inventory.cached.quantity_of('fish_bait'):
            mapping = self.FISH_CHANCES_WITH_BAIT
            await inventory.add_item('fish_bait', -1)
        else:
            mapping = self.FISH_CHANCES

        await record.add_random_exp(12, 18, chance=0.8)
        await record.add_random_bank_space(10, 15, chance=0.6)

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

    RARE_DIG_ITEMS = {
        Items.hook_worm,
        Items.poly_worm,
        Items.ancient_relic,
    }

    DIG_PROMPTS = (
        "dig dig dig",
        "my shovel is about to break",
        "must dig faster",
        "probably dulled out my shovel",
        "what must this be?",
        "oh look, something shiny?",
        "this must be something special",
    )

    @command(aliases={'shovel', 'di'})
    @simple_cooldown(1, 30)
    @user_max_concurrency(1)
    async def dig(self, ctx: Context):
        """Dig up items from the ground and sell them for profit!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()

        try:
            shovel = next(filter(inventory.cached.quantity_of, Items.__shovels__))
        except StopIteration:
            yield f'You need {Items.shovel.get_sentence_chunk(1)} to dig.', BAD_ARGUMENT
            return

        mapping = shovel.metadata

        items = random.choices(list(mapping), weights=list(mapping.values()), k=7)
        items = {item: items.count(item) for item in set(items) if item is not None}

        await record.add_random_exp(12, 18, chance=0.8)
        await record.add_random_bank_space(10, 15, chance=0.6)

        yield f'{Emojis.loading} Digging through the ground using your {shovel.name}...', REPLY
        await asyncio.sleep(random.uniform(2., 4.))

        if not len(items):
            yield 'You dug up absolutely nothing. Lmao.', EDIT
            return

        if any(item in self.RARE_DIG_ITEMS for item in items):
            message = random.choice(self.DIG_PROMPTS)

            yield (
                f'You found something out of the ordinary! Type `{insert_random_u200b(message)}` to dig it up before it breaks.',
                EDIT,
            )

            try:
                response = await ctx.bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=10)
                initial = "You failed dig up the item"

            except asyncio.TimeoutError:
                response = ctx.message  # guaranteed that this wont equal the message
                initial = "You couldn't type out your prompt in time"

            if response.content.lower() != message:
                await inventory.add_item(shovel, -1)

                if random.random() < 0.15:
                    await record.make_dead(reason='being buried alive')

                    yield f'{initial}, and the mound of dirt you have dug up beforehand collapses in on you, burying yourself alive. You suffocate to death.', REPLY
                    return

                yield f'{initial}. You try your best to dig the item up, but your shovel suddenly snaps in half! Whoops.', REPLY
                return

        async with ctx.db.acquire() as conn:
            for item, count in items.items():
                await inventory.add_item(item, count, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)

        embed.add_field(name='You dug up:', value='\n'.join(f'{item.get_display_name(bold=True)} x{count}' for item, count in items.items()))
        embed.set_author(name=f'Digging: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_footer(text=f'Used {shovel.name}')

        yield '', embed, EDIT

    RARE_ORES = {
        Items.gold,
        Items.obsidian,
        Items.emerald,
        Items.diamond,
    }

    MINE_PROMPTS = (
        "mine mine mine",
        "my pickaxe is about to break",
        "what a shiny ore",
        "this must be something special",
        "oh look, something shiny?",
        "what must this be?",
        "that looks like a cool ore",
    )

    @command(aliases={'pickaxe', 'm'})  # TODO: so much boilerplate within these commands, maybe make a common function for these?
    @simple_cooldown(1, 30)
    @user_max_concurrency(1)
    async def mine(self, ctx: Context):
        """Mine ores from deep below the ground and sell them for profit!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()

        try:
            pickaxe = next(filter(inventory.cached.quantity_of, Items.__pickaxes__))
        except StopIteration:
            yield f'You need {Items.pickaxe.get_sentence_chunk(1)} to mine.', BAD_ARGUMENT
            return

        mapping = pickaxe.metadata

        items = random.choices(list(mapping), weights=list(mapping.values()), k=6)
        items = {item: items.count(item) for item in set(items) if item is not None}

        await record.add_random_exp(12, 18, chance=0.8)
        await record.add_random_bank_space(10, 15, chance=0.6)

        yield f'{Emojis.loading} Mining using your {pickaxe.name}...', REPLY
        await asyncio.sleep(random.uniform(2., 4.))

        if not len(items):
            yield 'You mined absolutely nothing. Lmao.', EDIT
            return

        if any(item in self.RARE_ORES for item in items):
            message = random.choice(self.MINE_PROMPTS)

            yield (
                f'Ooh, the ore you mined looks special! Type `{insert_random_u200b(message)}` to retrieve the ore.',
                EDIT,
            )

            try:
                response = await ctx.bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=10)
                initial = "You failed mine the ore"

            except asyncio.TimeoutError:
                response = ctx.message  # guaranteed that this wont equal the message
                initial = "You couldn't type out your prompt in time"

            if response.content.lower() != message:
                await inventory.add_item(pickaxe, -1)

                if random.random() < 0.15:
                    await record.make_dead(reason='pickaxe snapping back on you')

                    yield f'{initial}. Your pickaxe snaps and the sharp part comes flying back at you, impaling your chest. You died.', REPLY
                    return

                yield f'{initial}, and your pickaxe snaps in half while trying to mine the ore.', REPLY
                return

        async with ctx.db.acquire() as conn:
            for item, count in items.items():
                await inventory.add_item(item, count, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)

        embed.add_field(name='You mined:', value='\n'.join(f'{item.get_display_name(bold=True)} x{count}' for item, count in items.items()))
        embed.set_author(name=f'Mining: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_footer(text=f'Used {pickaxe.name}')

        yield '', embed, EDIT

    ABUNDANCE_FOREST_WOOD_CHANCES = {
        None: 1,
        Items.wood: 0.3,
        Items.redwood: 0.03,
        Items.blackwood: 0.0025,
    }

    EXOTIC_FOREST_WOOD_CHANCES = {
        None: 1,
        Items.wood: 0.5,
        Items.redwood: 0.09,
        Items.blackwood: 0.0085,
    }

    @command(aliases={'c', 'ch', 'axe'})
    @simple_cooldown(1, 25)
    @user_max_concurrency(1)
    async def chop(self, ctx: Context):
        """Chop down trees for wood! Wood can be sold for profit, or used to craft many items."""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()

        if not inventory.cached.quantity_of('axe'):
            yield f'You need {Items.axe.get_sentence_chunk(1)} to chop down trees.', BAD_ARGUMENT
            return

        view = ChopView(ctx)
        yield (
            "Choose a forest to chop trees from. Abundance Forest has a lower chance of getting rarer wood, while Exotic Forest has a higher chance.\n"
            "However, there is a chance that you could die by chopping down a tree in the Exotic Forest.",
            view,
            REPLY,
        )

        await view.wait()
        if view.choice is None:
            yield 'Timed out.', REPLY
            return

        # random.random() is INCLUSIVE of 0, but EXCLUSIVE of 1
        success_chance = 0.95 if view.choice == view.EXOTIC else 1
        mapping = self.ABUNDANCE_FOREST_WOOD_CHANCES if view.choice == view.ABUNDANCE else self.EXOTIC_FOREST_WOOD_CHANCES

        wood = random.choices(list(mapping), weights=list(mapping.values()), k=13)
        wood = {item: wood.count(item) for item in set(wood) if item is not None}

        await record.add_random_exp(12, 18, chance=0.8)
        await record.add_random_bank_space(10, 15, chance=0.6)

        yield f'{Emojis.loading} Chopping down some trees...', REPLY
        await asyncio.sleep(random.uniform(2., 4.))

        if not len(wood):
            yield 'You couldn\'t chop down any trees, lol.', EDIT
            return

        if random.random() > success_chance:
            await record.make_dead(reason='a tree falling on your head')
            yield 'How exotic! A tree fell on your head while you were chopping it down, killing you instantly.', EDIT
            return

        # TODO: way to make user lose their axe?

        async with ctx.db.acquire() as conn:
            for item, count in wood.items():
                await inventory.add_item(item, count, connection=conn)

        embed = discord.Embed(color=Colors.success, timestamp=ctx.now)
        embed.add_field(name='You generated:', value='\n'.join(f'{item.get_display_name(bold=True)} x{count}' for item, count in wood.items()))
        embed.set_author(name=f'Chopping: {ctx.author}', icon_url=ctx.author.avatar.url)

        yield '', embed, EDIT

    async def pop_trivia_question(self) -> TriviaQuestion:
        try:
            return self._trivia_questions.popleft()
        except IndexError:
            pass

        async with self._trivia_questions_fetch_lock:
            # async with self.bot.session.get('https://opentdb.com/api.php?amount=50') as response:
            #     if not response.ok:
            #         raise RuntimeError('failed to retrieve trivia question')
            #
            #     data = await response.json(encoding='utf-8')

            # aiohttp does not work with this on windows due to the fact that aiohttp utilizes OS certs
            response = await asyncio.to_thread(requests.get, 'https://opentdb.com/api.php?amount=50')
            if not response.ok:
                raise RuntimeError('failed to retrieve trivia question')

            data = await asyncio.to_thread(response.json)

            if data['response_code'] != 0:
                raise RuntimeError('failed to retrieve trivia question')

            self._trivia_questions.extend(TriviaQuestion.from_data(q) for q in data['results'])
            return self._trivia_questions.popleft()

    TRIVIA_PRIZE_MAPPING = {
        'easy': (100, 150),
        'medium': (200, 325),
        'hard': (360, 500),
    }

    @command(aliases={'triv', 'tv'})
    @simple_cooldown(1, 20)
    @user_max_concurrency(1)
    async def trivia(self, ctx: Context):
        """Answer trivia questions for coins!"""
        question = await self.pop_trivia_question()
        prize = random.randint(*self.TRIVIA_PRIZE_MAPPING[question.difficulty])

        embed = discord.Embed(color=Colors.primary, description=question.question, timestamp=ctx.now)
        embed.set_author(name=f'Trivia: {ctx.author}', icon_url=ctx.author.avatar.url)
        embed.set_footer(text='Answer using the buttons below!')

        embed.add_field(name='Difficulty', value=question.difficulty.title())
        embed.add_field(name='Category', value=question.category)
        embed.add_field(name='Prize', value=f'{Emojis.coin} **{prize:,}**')

        view = TriviaView(ctx, embed, question)
        yield embed, view, REPLY

        await view.wait()
        if not view.choice:
            yield f"You didn't answer in time! The correct answer was **{question.correct_answer}**", REPLY
            return

        record = await ctx.db.get_user_record(ctx.author.id)

        async with ctx.db.acquire() as conn:
            await record.add_random_bank_space(10, 15, chance=0.5, connection=conn)
            await record.add_random_exp(10, 15, chance=0.65, connection=conn)

            if view.choice == question.correct_answer:
                profit = await record.add_coins(prize)
                yield f'Correct! You earned {Emojis.coin} **{profit:,}**.', REPLY
                return

        yield f'Wrong, the correct answer was **{question.correct_answer}**', REPLY

    @command(aliases={'da', 'day'})
    @database_cooldown(86_400)
    @user_max_concurrency(1)
    @cooldown_message('This command is named daily for a reason.')
    async def daily(self, ctx: Context) -> tuple[discord.Embed, Any]:
        """Claim your daily reward!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        cooldowns = await record.cooldown_manager.wait()

        previous = cooldowns.cached['daily'].previous_expiry

        if previous and ctx.now - previous <= timedelta(days=1):  # Give one day of breathing room
            await record.add(daily_streak=1)
        else:
            await record.update(daily_streak=0)

        streak_benefit = record.daily_streak * 250
        profit = 5000 + streak_benefit

        await record.add(wallet=profit)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name}: Claim Daily', icon_url=ctx.author.avatar.url)

        if streak_benefit:
            embed.add_field(name='Streak Bonus', value=f'+{Emojis.coin} **{streak_benefit:,}** [Streak: {record.daily_streak}]')

        embed.description = f'You claimed your daily reward of {Emojis.coin} **{profit:,}**.'

        return embed, REPLY

    @command(aliases={'week', 'wk'})
    @database_cooldown(604_800)
    @user_max_concurrency(1)
    @cooldown_message('This command is named weekly for a reason.')
    async def weekly(self, ctx: Context) -> tuple[discord.Embed, Any]:
        """Claim your weekly reward!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        cooldowns = await record.cooldown_manager.wait()

        previous = cooldowns.cached['weekly'].previous_expiry

        if previous and ctx.now - previous <= timedelta(days=2):  # Give two days of breathing room
            await record.add(weekly_streak=1)
        else:
            await record.update(weekly_streak=0)

        streak_benefit = record.weekly_streak * 2000
        profit = 20000 + streak_benefit

        await record.add(wallet=profit)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name}: Claim Weekly', icon_url=ctx.author.avatar.url)

        if streak_benefit:
            embed.add_field(name='Streak Bonus', value=f'+{Emojis.coin} **{streak_benefit:,}** [Streak: {record.weekly_streak}]')

        embed.description = f'You claimed your weekly reward of {Emojis.coin} **{profit:,}**.'

        return embed, REPLY

    def store_rob(self, ctx: Context, victim: AnyUser, amount: int) -> RobData:
        self._recent_robs[victim.id] = entry = RobData(
            timestamp=ctx.utcnow(), robbed_by=ctx.author, victim=victim, amount=amount,
        )
        return entry

    @command(aliases={'steal', 'ripoff'})
    @simple_cooldown(1, 90)
    @user_max_concurrency(1)
    @lock_transactions
    async def rob(self, ctx: Context, *, user: CaseInsensitiveMemberConverter):
        # sourcery no-metrics skip: merge-nested-ifs
        """Attempt to rob someone of their coins! There is a chance that you might fail and pay a fine, or even die."""
        if user == ctx.author:
            yield 'What are you trying to do? Rob yourself? Sounds kinda dumb to me.', BAD_ARGUMENT
            return

        if user.bot:
            yield 'You cannot rob bot accounts.', BAD_ARGUMENT
            return

        if entry := self._recent_robs.get(user.id):
            if ctx.now - entry.timestamp < timedelta(minutes=3):
                yield 'That user has recently been robbed, let\'s give them a break.', BAD_ARGUMENT
                return

        lock = ctx.bot.transaction_locks.setdefault(user.id, LockWithReason())

        if lock.locked():
            yield f'{user.name} is currently being robbed, lmao', BAD_ARGUMENT
            return

        their_record = await ctx.db.get_user_record(user.id)

        if their_record.wallet < 500:
            yield f"The person you're trying to rob is pretty poor, try robbing people with more than {Emojis.coin} 500 next time.", BAD_ARGUMENT
            return

        record = await ctx.db.get_user_record(ctx.author.id)

        if record.wallet < 500:
            yield f'You must have {Emojis.coin} 500 in your wallet in order to rob someone.', BAD_ARGUMENT
            return

        skills = await record.skill_manager.wait()
        their_skills = await their_record.skill_manager.wait()

        success_chance = max(50 + skills.points_in('robbery') - their_skills.points_in('defense') * 1.5, 2) / 100

        if not await ctx.confirm(
            f'Are you sure you want to rob **{user.name}**? (Success chance: {success_chance:.0%})',
            delete_after=True,
            reference=ctx.message,
        ):
            yield 'Looks like we won\'t rob today.', BAD_ARGUMENT
            return

        async with lock.with_reason(
            f"Someone else ({ctx.author.mention}) is currently trying to rob you - view your notifications to find out more details!"
        ):
            notify = their_record.notifications_manager.add_notification

            async with ctx.db.acquire() as conn:
                await record.add_random_bank_space(10, 15, chance=0.6, connection=conn)
                await record.add_random_exp(12, 17, chance=0.7, connection=conn)

            yield f'{Emojis.loading} Robbing {user.name}...', REPLY
            await asyncio.sleep(random.uniform(1.5, 3.5))

            padlock_worked = False

            if their_record.padlock_active:
                padlock_worked = True
                inventory = await record.inventory_manager.wait()

                if inventory.cached.quantity_of('key') > 0 and await ctx.confirm(
                    f'{Items.padlock.emoji} {user.name} has a padlock active!\n'
                    f'You have a {Items.key.get_display_name(bold=True)} in your inventory, do you want to use it to potentially open the padlock?',
                    reference=ctx.message,
                ):
                    await inventory.add_item('key', -1)
                    if random.random() < 0.25:
                        padlock_worked = False
                        await their_record.update(padlock_active=False)
                        yield f'{Items.padlock.emoji} Unlocked {user.name}\'s padlock!', REPLY

                        await notify(
                            title='Someone unlocked your padlock!',
                            content=f'{ctx.author.mention} used a {Items.key.get_display_name(bold=True)} to unlock your padlock!',
                        )
                    else:
                        yield f'{Items.padlock.emoji} Failed to unlock {user.name}\'s padlock! (You also consumed your key)', REPLY

            if padlock_worked:
                fine_percent = random.uniform(.05, .25)
                fine = max(500, round(record.wallet * fine_percent))

                fine_percent = fine / record.wallet
                yield (
                    f'{Items.padlock.emoji} {user.name} had a padlock active. '
                    f'You were instantly caught trying to get rid of the padlock and you pay a fine of {Emojis.coin} **{fine:,}** ({fine_percent:.1%} of your wallet).',
                    EDIT,
                )
                await record.add(wallet=-fine)
                await their_record.update(padlock_active=False)

                await notify(
                    title='Someone tried to rob you, but you had a padlock active!',
                    content=f'{ctx.author.mention} tried robbing you in **{ctx.guild.name}**, but failed due to your padlock being active. Your padlock is now deactivated.',
                )
                return

            await notify(
                title='You are being robbed!',
                content=f'{ctx.author.mention} is trying to rob you in **{ctx.guild.name}**!',
            )

            embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
            embed.set_author(name=f'{ctx.author.name}: Robbing {user.name}', icon_url=ctx.author.avatar.url)

            code = random.randint(100000, 999999)
            embed.description = (
                "Robbing isn't as always as easy as it seems. Quick! Type in the following combination onto the keypad below "
                f"before time runs out to rob {user.mention} of their coins!\n\n"
                f"{user.mention}, you can press the **CATCH!** button before {ctx.author.name} finishes entering in the "
                f"combination in order to catch them and automatically fail their attempt."
            )

            embed.add_field(name='Enter the following combination:', value=str(code), inline=False)
            view = RobbingKeypad(ctx, user, embed, code)

            yield '', embed, view, EDIT

            try:
                await asyncio.wait_for(view.wait(), timeout=20)
            except asyncio.TimeoutError:
                fine_percent = random.uniform(.1, .5)
                fine = max(500, round(record.wallet * fine_percent))

                fine_percent = fine / record.wallet
                yield (
                    f'Looks like you took too long to enter in the combination. '
                    f'You were caught trying to break into {user.name}\'s wallet and you pay a fine of {Emojis.coin} **{fine:,}** ({fine_percent:.1%} of your wallet).',
                    REPLY,
                )
                await record.add(wallet=-fine)

                await notify(
                    title='Someone tried to rob you, but failed!',
                    content=f'{ctx.author.mention} tried robbing you in **{ctx.guild.name}**, but failed due to taking too long to enter in the combination.',
                )
                return

            if view.caught:
                fine_percent = random.uniform(.2, .6)
                fine = max(500, round(record.wallet * fine_percent))

                fine_percent = fine / record.wallet
                yield (
                    f'{user.name} caught you trying to break into their wallet and immediately call the cops on you. '
                    f'You pay a fine of {Emojis.coin} **{fine:,}** ({fine_percent:.1%} of your wallet).',
                    REPLY,
                )
                await record.add(wallet=-fine)
                return  # Don't notify here since that person MUST have been present

            if str(code) != view.entered:
                fine_percent = random.uniform(.1, .5)
                fine = max(500, round(record.wallet * fine_percent))

                fine_percent = fine / record.wallet
                yield (
                    f'You entered in the wrong combination. '
                    f'The police are alerted about your attempt and you pay a fine of {Emojis.coin} **{fine:,}** ({fine_percent:.1%} of your wallet).',
                    REPLY,
                )
                await record.add(wallet=-fine)

                await notify(
                    title='Someone tried to rob you, but failed!',
                    content=f'{ctx.author.mention} tried robbing you in **{ctx.guild.name}**, but failed due to entering in the wrong combination.',
                )
                return

            embed.colour = Colors.success
            yield embed, EDIT

            death_chance = max(10 - skills.points_in('robbery') / 2 + their_skills.points_in('defense') / 2, 0) / 100

            if random.random() < success_chance:
                payout_percent = min(
                    random.uniform(.3, .8) + min(skills.points_in('robbery') * .02, .5), 1,
                    )
                payout = round(their_record.wallet * payout_percent)
                await record.add(wallet=payout)
                await their_record.add(wallet=-payout)

                yield (
                    f"**SUCCESS!** You stole {Emojis.coin} **{payout:,}** ({payout_percent:.1%}) from {user.name}'s wallet.\n"
                    f"You now have {Emojis.coin} **{record.wallet:,}**.",
                    REPLY,
                )

                self.store_rob(ctx, user, payout)

                await notify(
                    title='Someone stole coins from you!',
                    content=f'{ctx.author.mention} stole {Emojis.coin} **{payout:,}** ({payout_percent:.1%}) from your wallet in **{ctx.guild.name}**!',
                )
                return

            if random.random() < death_chance:
                await record.make_dead()
                yield (
                    f"While trying your best not to make a noise, you are spotted by police while trying to rob {user.name}.\n"
                    "You refuse arrest causing the police to fatally shoot you. You died.",
                    REPLY,
                )

                await notify(
                    title='Someone tried to rob you, but died in the process!',
                    content=f'{ctx.author.mention} tried robbing you in **{ctx.guild.name}**, but died due to being caught by the police.',
                )
                return

            # highest fines are here
            fine_percent = random.uniform(.2, .7)
            fine = max(500, round(record.wallet * fine_percent))

            fine_percent = fine / record.wallet
            yield (
                f'While so stealthily trying to rob {user.name}, you are spotted by police, '
                f'who force you to pay a fine of {Emojis.coin} **{fine:,}** ({fine_percent:.1%} of your wallet) to {user.name}.',
                REPLY,
            )
            await record.add(wallet=-fine)
            await their_record.add(wallet=fine)

            await notify(
                title='Someone tried to rob you!',
                content=f'{ctx.author.mention} tried robbing you in **{ctx.guild.name}**, but was spotted by police. They paid you a fine of {Emojis.coin} **{fine:,}**.',
            )
            return


class ChopView(UserView):
    ABUNDANCE = 0
    EXOTIC = 1

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx.author, timeout=20)

        self.choice: Literal[0, 1] | None = None
        self._ctx: Context = ctx

    def _disable_buttons(self) -> None:
        for button in self.children:
            assert isinstance(button, discord.ui.Button)
            button.disabled = True

    @discord.ui.button(label='Abundance Forest')
    async def abundance(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        self.choice = self.ABUNDANCE

        self._disable_buttons()
        button.style = discord.ButtonStyle.primary

        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label='Exotic Forest')
    async def exotic(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        self.choice = self.EXOTIC

        self._disable_buttons()
        button.style = discord.ButtonStyle.primary

        await interaction.response.edit_message(view=self)
        self.stop()


class TriviaButton(discord.ui.Button['TriviaView']):
    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.choice = self.label

        color = Colors.success if self.view.correct == self.label else Colors.error
        self.view.embed.colour = color

        for button in self.view.children:
            assert isinstance(button, discord.ui.Button)

            if button.label == self.view.correct:
                button.style = discord.ButtonStyle.success

            elif button.label == self.label:
                button.style = discord.ButtonStyle.danger

            else:
                button.style = discord.ButtonStyle.secondary

            button.disabled = True

        await interaction.response.edit_message(embed=self.view.embed, view=self.view)

        self.view.stop()


class TriviaView(UserView):
    def __init__(self, ctx: Context, embed: discord.Embed, question: TriviaQuestion) -> None:
        super().__init__(ctx.author, timeout=15)

        if question.type == 'boolean':
            self.add_item(TriviaButton(label='True', style=discord.ButtonStyle.success))
            self.add_item(TriviaButton(label='False', style=discord.ButtonStyle.danger))
        else:
            for answer in question.answers:
                self.add_item(TriviaButton(label=answer, style=discord.ButtonStyle.primary))

        self.embed: discord.Embed = embed
        self.correct: str = question.correct_answer
        self.choice: str | None = None


class PlaceholderKeypadButton(discord.ui.Button['RobbingKeypad']):
    def __init__(self, *, row: int | None = None) -> None:
        super().__init__(label='\u200b', disabled=True, row=row)


class RobbingKeypad(discord.ui.View):
    def __init__(self, ctx: Context, opponent: AnyUser, embed: discord.Embed, code: int) -> None:
        super().__init__()

        self.ctx: Context = ctx
        self.opponent: AnyUser = opponent

        self.code: int = code
        self.embed: discord.Embed = embed
        self.entered: str = ''

        self.caught: bool = False
        self.dangling_interaction: discord.Interaction | None = None

        self.clear_button = discord.ui.Button(label='Clear', style=discord.ButtonStyle.danger, row=4)
        self.submit_button = discord.ui.Button(label='Submit!', style=discord.ButtonStyle.success, row=4)
        self.catch_button = discord.ui.Button(label='CATCH!', style=discord.ButtonStyle.danger, row=4)

        self.clear_button.callback = self.clear_callback
        self.submit_button.callback = self.submit_callback
        self.catch_button.callback = self.catch_callback

        self.add_buttons()

    def update(self) -> None:
        self.embed.remove_field(1)

        if self.entered:
            self.embed.add_field(name='You entered:', value=f'```py\n{self.entered}```', inline=False)

    def add_buttons(self) -> None:
        buttons = (
            (1, 2, 3),
            (4, 5, 6),
            (7, 8, 9),
            (None, 0, None),
        )

        self.clear_items()
        for i, row in enumerate(buttons):
            for button in row:
                self.add_item(
                    PlaceholderKeypadButton(row=i)
                    if button is None
                    else RobberyTrainingButton(button, row=i, user=self.ctx.author)
                )

        self.add_item(self.clear_button)
        self.add_item(self.submit_button)
        self.add_item(self.catch_button)

    async def clear_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message('nope', ephemeral=True)

        self.entered = ''
        self.update()

        await interaction.response.edit_message(embed=self.embed, view=self)

    async def submit_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message('nope', ephemeral=True)

        self.dangling_interaction = interaction
        self.stop()

    async def catch_callback(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.opponent:
            return await interaction.response.send_message(f'Only {self.opponent.mention} can use this button.', ephemeral=True)

        self.caught = True
        self.dangling_interaction = interaction
        self.stop()


setup = Profit.simple_setup

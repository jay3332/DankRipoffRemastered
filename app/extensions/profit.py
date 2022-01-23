from __future__ import annotations

import asyncio
import random
from textwrap import dedent
from typing import NamedTuple, TYPE_CHECKING

import discord

from app.core import Cog, Context, EDIT, REPLY, command, simple_cooldown, user_max_concurrency
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

            yield embed, EDIT
            return

        profit = await record.add_coins(random.randint(150, 450))

        embed.colour = Colors.success
        embed.description = self._capitalize_first(
            random.choice(self.BEG_SUCCESS_MESSAGES).format(person, f'{Emojis.coin} **{profit:,}**')
        )

        yield embed, EDIT
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
                    {multiplier * 100:,.1f}% of initial value
                    \u2937 {Emojis.coin} +{round(amount * multiplier):,}
                """), inline=False)

                embed.add_field(name="Total Return", value=f"{Emojis.coin} {round(amount * (1 + multiplier)):,}")

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
            {multiplier * 100:,.1f}% of initial value
            \u2937 {Emojis.coin} +{round(amount * multiplier):,}
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
                # TODO: add death
                embed.add_field(name='You died!', value=random.choice(choice.death_responses))

                yield embed, REPLY
                return

            message = random.choice(choice.failure_responses)
            embed.add_field(name='You found nothing!', value=message)

            yield embed, REPLY
            return

        profit = await record.add_coins(random.randint(choice.minimum, choice.maximum))

        embed.colour = Colors.success
        embed.add_field(name='Profit!', value=random.choice(choice.success_responses).format(f'{Emojis.coin} **{profit:,}**'))

        yield embed, REPLY


def setup(bot: Bot) -> None:
    bot.add_cog(Profit(bot))

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import discord

from app.core import Cog, Context, EDIT, command, simple_cooldown
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


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
    async def beg(self, ctx: Context) -> discord.Embed:
        """Beg for coins. There is a chance that you can get nothing, and a small chance that you can obtain some items"""
        yield f"{Emojis.loading} {random.choice(self.BEG_INITIAL_MESSAGES)}"
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


def setup(bot: Bot) -> None:
    bot.add_cog(Profit(bot))

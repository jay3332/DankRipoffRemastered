
from __future__ import annotations

import asyncio
import random
from textwrap import dedent
from typing import Any, TYPE_CHECKING

import discord

from app.core import Cog, Context, EDIT, REPLY, command, lock_transactions, simple_cooldown, user_max_concurrency
from app.util.converters import CasinoBet
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


class Casino(Cog):
    """Gamble off all of your coins at the casino!"""

    @staticmethod
    def _format_roll(roll: list[int]) -> str:
        assert len(roll) == 2

        first, second = roll
        return f"{Emojis.dice[first]} {Emojis.dice[second]}"

    @command(aliases={'diceroll', 'r', 'bet', 'gamble'})
    @simple_cooldown(1, 25)
    @user_max_concurrency(1)
    @lock_transactions
    async def roll(self, ctx: Context, *, bet: CasinoBet()) -> Any:
        """You and I each will roll a pair of dice. The one with the higher sum wins!"""
        record = await ctx.db.get_user_record(ctx.author.id)

        yield f'{Emojis.loading} Rolling...', REPLY
        await asyncio.sleep(random.uniform(2, 4))

        async with ctx.db.acquire() as conn:
            await record.add_random_exp(10, 15, chance=0.5, connection=conn)
            await record.add_random_bank_space(10, 15, chance=0.5, connection=conn)

        their_dice = random.choices(range(1, 6), k=2)
        my_dice = random.choices(range(1, 6), k=2)

        their_sum = sum(their_dice)
        my_sum = sum(my_dice)

        embed = discord.Embed(timestamp=ctx.now)
        embed.add_field(name=f'Your Roll ({their_sum})', value=self._format_roll(their_dice), inline=True)
        embed.add_field(name=f'My Roll ({my_sum})', value=self._format_roll(my_dice), inline=True)

        if their_sum > my_sum:
            base_multiplier = random.uniform(0.55, 0.95)
            profit = await record.add_coins(round(bet * base_multiplier))

            embed.colour = Colors.success
            embed.set_author(name='Winner!', icon_url=ctx.author.avatar.url)

            embed.add_field(name=f'You won {Emojis.coin} **{profit:,}**!', inline=False, value=dedent(f"""
                Multiplier: **{base_multiplier:.1%}**
                You now have {Emojis.coin} **{record.wallet:,}**.
            """))

        elif their_sum == my_sum:
            embed.colour = Colors.warning
            embed.set_author(name='Tie!', icon_url=ctx.author.avatar.url)
            embed.add_field(name='**We tied!**', value='You get absolutely nothing, try again next time.', inline=False)

        else:
            await record.add(wallet=-bet)

            embed.colour = Colors.error
            embed.set_author(name='Loser!', icon_url=ctx.author.avatar.url)

            embed.add_field(name='**You lost!**', inline=False, value=dedent(f"""
                You lost {Emojis.coin} **{bet:,}**.
                You now have {Emojis.coin} **{record.wallet:,}**.
            """))

            embed.set_footer(text='Better luck next time!')

        yield '', embed, EDIT


def setup(bot: Bot) -> None:
    bot.add_cog(Casino(bot))

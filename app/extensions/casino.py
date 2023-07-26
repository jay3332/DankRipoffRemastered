
from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from enum import Enum
from textwrap import dedent
from typing import Any, ClassVar, Final, NamedTuple, TYPE_CHECKING

import discord

from app.core import Cog, Context, EDIT, REPLY, command, group, lock_transactions, simple_cooldown, user_max_concurrency
from app.util.common import pluralize
from app.util.converters import CasinoBet
from app.util.views import UserView
from config import Colors, Emojis


class ScratchCell(Enum):
    empty = 0
    lose = 1
    coin = 2
    medal = 3
    trophy = 4
    crown = 5
    star = 6
    spinning_coin = 7


class ScratchCellInfo(NamedTuple):
    emoji: str | None
    chance: float
    max_occurences: int | None = None
    style: discord.ButtonStyle = discord.ButtonStyle.primary
    profit: float | None = None


class ScratchButton(discord.ui.Button['ScratchView']):
    def __init__(self, cell: ScratchCell, info: ScratchCellInfo, row: int):
        self.cell: ScratchCell = cell
        self.info: ScratchCellInfo = info

        super().__init__(style=discord.ButtonStyle.secondary, row=row, label='\u200b')

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.scratches -= 1

        match self.cell, self.info:
            case ScratchCell.empty, _:
                message = 'Empty cell.'
            case ScratchCell.lose, _:
                message = f'{self.info.emoji} You scratched a skull and you lose your bet.'
            case _, info:
                self.view.multiplier += info.profit
                message = f'[ Scratched {info.emoji} ] {Emojis.coin} **+{self.view.bet * info.profit:,.0f}** (+{info.profit:.0%})'
            case _:
                raise

        self.emoji = self.info.emoji
        self.style = self.info.style

        self.view.description.append(f'({5 - self.view.scratches}) {message}')
        self.view.update_embed()

        if self.view.scratches <= 0 or self.cell is ScratchCell.lose:
            self.view.disable_items()

            if self.cell is ScratchCell.lose:
                self.view.multiplier = 0
                message = 'You scratched off a skull emoji.'
            else:
                message = 'You did not scratch off any profitable cells.'

            if not self.view.multiplier:
                self.view.embed.colour = Colors.error

                self.view.embed.clear_fields()
                self.view.embed.add_field(name='You got nothing!', value=message + f'\nYou now have {Emojis.coin} **{self.view.record.wallet:,}**.')

            else:
                self.view.embed.clear_fields()
                gain = await self.view.record.add_coins(self.view.bet * self.view.multiplier)
                profit = gain - self.view.bet

                self.view.embed.colour = Colors.success if profit > 0 else Colors.warning

                self.view.embed.add_field(
                    name=f'You got {Emojis.coin} **{gain:,}**! ({self.view.multiplier:.0%})',
                    value=f'Profit: {Emojis.coin} **{profit:+,}**\nYou now have {Emojis.coin} **{self.view.record.wallet:,}**.'
                )

            self.view.stop()

        await interaction.response.edit_message(embed=self.view.embed, view=self.view)


class ScratchView(UserView):
    SCRATCH_CELLS: Final[ClassVar[dict[ScratchCell, ScratchCellInfo]]] = {
        ScratchCell.empty: ScratchCellInfo(emoji=None, chance=0.5),
        ScratchCell.lose: ScratchCellInfo(emoji='\U0001f480', chance=0, max_occurences=1, style=discord.ButtonStyle.red),  # add manually
        ScratchCell.coin: ScratchCellInfo(emoji='<:coin:896432147152400394>', chance=0.24, max_occurences=8, profit=0.3),
        ScratchCell.medal: ScratchCellInfo(emoji='\U0001f3c5', chance=0.1, max_occurences=4, profit=0.5),
        ScratchCell.trophy: ScratchCellInfo(emoji='\U0001f3c6', chance=0.04, max_occurences=3, profit=0.8),
        ScratchCell.crown: ScratchCellInfo(emoji='\U0001f451', chance=0.015, max_occurences=3, profit=1.2),
        ScratchCell.star: ScratchCellInfo(emoji='\U0001f320', chance=0.0045, max_occurences=2, profit=2),
        ScratchCell.spinning_coin: ScratchCellInfo(emoji='<a:spinning_coin:939937188836147240>', chance=0.001, max_occurences=1, profit=4),
    }

    if TYPE_CHECKING:
        cells: list[list[ScratchCell]]

    def __init__(self, ctx: Context, bet: int) -> None:
        super().__init__(ctx.author, timeout=120)
        self.fill_cells()
        self.update_buttons()
        self.record = ctx.db.get_user_record(ctx.author.id, fetch=False)

        self.scratches: int = 5
        self.multiplier: float = 0
        self.bet: int = bet
        self.description: list[str] = []

        self.embed = embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.description = f'Press a button to scratch it off! You have {self.scratches} scratches left.'
        embed.set_author(name=f'{ctx.author.name}: Scratch-off ticket', icon_url=ctx.author.avatar.url)
        embed.set_footer(text=f'Run "{ctx.clean_prefix}scratch key" to see what the symbols mean.')

    def fill_cells(self):
        cells = []
        occurences = defaultdict(int)

        for _ in range(5):
            row = []
            for _ in range(3):
                cell = random.choices(*zip(*(
                    (key, info.chance) for key, info in self.SCRATCH_CELLS.items()
                    if info.max_occurences is None or occurences[key] < info.max_occurences
                )))[0]

                occurences[cell] += 1
                row.append(cell)

            cells.append(row)

        cells[random.randint(0, 4)][random.randint(0, 2)] = ScratchCell.lose
        self.cells = cells

    def update_buttons(self) -> None:
        self.clear_items()

        for i, row in enumerate(self.cells):
            for cell in row:
                self.add_item(ScratchButton(cell, self.SCRATCH_CELLS[cell], i))

    def update_embed(self) -> None:
        self.embed.description = '\n'.join(self.description)
        if self.scratches:
            self.embed.description += pluralize(f'\nYou have {self.scratches} scratch(es) left.')

        if self.multiplier:
            self.embed.remove_field(0)
            self.embed.add_field(name='Return', value=f'{Emojis.coin} {self.bet * self.multiplier:,.0f} ({self.multiplier:.0%})')

    def disable_items(self) -> None:
        for child in self.children:
            assert isinstance(child, ScratchButton)

            if not child.disabled:
                child.disabled = True
                child.emoji = child.info.emoji


class Casino(Cog):
    """Gamble off all of your coins at the casino!"""

    emoji = '\U0001f911'

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
            await record.add_random_exp(10, 15, chance=0.5, ctx=ctx, connection=conn)
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

    @group(aliases={'scratchticket', 'scratch-ticket', 'scratchoff', 'scr'})
    @simple_cooldown(1, 25)
    @user_max_concurrency(1)
    @lock_transactions
    async def scratch(self, ctx: Context, *, bet: CasinoBet(500)) -> Any:
        """Scratch a scratch-off ticket and hope for profit."""
        record = await ctx.db.get_user_record(ctx.author.id)
        await record.add(wallet=-bet)

        view = ScratchView(ctx, bet)
        yield view.embed, view, REPLY
        await view.wait()

    @scratch.command('key', aliases={'table', 'k'})
    @simple_cooldown(2, 4)
    async def scratch_key(self, ctx: Context) -> None:
        """View what emojis correspond to what in scratch-off tickets."""
        cells = ScratchView.SCRATCH_CELLS

        def emoji_of(cell: ScratchCell) -> str:
            return cells[cell].emoji

        await ctx.send(dedent(f"""
            {emoji_of(ScratchCell.lose)} = Automatic loss
            {emoji_of(ScratchCell.coin)} = +30% of bet
            {emoji_of(ScratchCell.medal)} = +50% of bet
            {emoji_of(ScratchCell.trophy)} = +80% of bet
            {emoji_of(ScratchCell.crown)} = +120% of bet
            {emoji_of(ScratchCell.star)} = +200% (2x) of bet
            {emoji_of(ScratchCell.spinning_coin)} = +400% (4x) of bet
        """))


setup = Casino.simple_setup


from __future__ import annotations

import asyncio
import functools
import random
from collections import defaultdict
from enum import Enum
from textwrap import dedent
from typing import Any, ClassVar, Final, Generic, Iterable, Iterator, NamedTuple, TYPE_CHECKING, TypeVar

import discord
from discord.utils import format_dt

from app.core import Cog, Context, EDIT, REPLY, command, group, lock_transactions, simple_cooldown, user_max_concurrency
from app.data.items import Items
from app.util.common import pluralize
from app.util.converters import CasinoBet
from app.util.types import TypedInteraction
from app.util.views import UserView
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.database import UserRecord

CardT = TypeVar('CardT', bound='Card')


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

    async def callback(self, interaction: TypedInteraction) -> None:
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


class CardSuit(Enum):
    spades   = 0
    hearts   = 1
    diamonds = 2
    clubs    = 3

    def __str__(self) -> str:
        return self.name.title()

    def __repr__(self) -> str:
        return f'<CardSuit.{self.name}>'

    @property
    def emoji(self) -> str:
        return {
            CardSuit.spades: '\u2660',
            CardSuit.hearts: '\u2665',
            CardSuit.diamonds: '\u2666',
            CardSuit.clubs: '\u2663',
        }[self]


class CardRank(Enum):
    ace   = 1
    two   = 2
    three = 3
    four  = 4
    five  = 5
    six   = 6
    seven = 7
    eight = 8
    nine  = 9
    ten   = 10
    jack  = 11
    queen = 12
    king  = 13

    def __str__(self) -> str:
        match self:
            case CardRank.ace:
                return 'A'
            case CardRank.jack:
                return 'J'
            case CardRank.queen:
                return 'Q'
            case CardRank.king:
                return 'K'
            case _:
                return str(self.value)

    def __repr__(self) -> str:
        return f'<CardRank.{self.name}>'

    @property
    def rank_value(self) -> int:
        match self:
            case CardRank.jack | CardRank.queen | CardRank.king:
                return 10
            case _:
                return self.value


class Card(NamedTuple):
    suit: CardSuit
    rank: CardRank

    def __str__(self) -> str:
        return f'{self.rank} of {self.suit}'

    def __repr__(self) -> str:
        return f'<Card: {self}>'

    @property
    def display(self) -> str:
        return f'{self.suit.emoji} {self.rank}'


class Deck(Generic[CardT]):
    def __init__(self, count: int = 1, *, cls: type[CardT] = Card) -> None:
        self.cards: list[CardT] = []
        self.reset(count, cls=cls)

    def reset(self, count: int = 1, *, cls: type[CardT] = Card) -> None:
        self.cards.clear()
        for _ in range(count):
            self.cards.extend(self.generate(cls=cls))

    def shuffle(self) -> None:
        random.shuffle(self.cards)

    @staticmethod
    def generate(*, cls: type[CardT] = Card) -> Iterable[CardT]:
        for suit in CardSuit:
            for rank in CardRank:
                yield cls(suit, rank)

    def draw(self) -> CardT:
        return self.cards.pop()

    def draw_many(self, n: int) -> list[CardT]:
        return [self.draw() for _ in range(n)]

    def __len__(self) -> int:
        return len(self.cards)

    def __iter__(self) -> Iterator[CardT]:
        return iter(self.cards)

    def __repr__(self) -> str:
        return f'<Deck of {len(self.cards)}>'


class BlackjackCard(Card):
    @property
    def value(self) -> int:
        return self.rank.rank_value

    def get_favorable_value(self, current: int) -> int:
        """In blackjack, aces can be either 1 or 11 depending on the current total."""
        if self.rank is CardRank.ace:
            return 1 if current + 11 > 21 else 11
        return self.value


class BlackjackHand(NamedTuple):
    cards: list[BlackjackCard]

    @property
    def total(self) -> int:
        return functools.reduce(lambda total, card: total + card.get_favorable_value(total), self.cards, 0)

    @property
    def natural(self) -> bool:
        return self.total == 21 and len(self.cards) == 2

    def __repr__(self) -> str:
        return f'<BlackjackHand worth {self.total}: {self.cards}>'

    @property
    def full_display(self) -> str:
        """Displays the hand with all cards visible."""
        return ' '.join(f'`{card.display}`' for card in self.cards)

    @property
    def hidden_display(self) -> str:
        """Displays the hand with only the first card visible."""
        return ' '.join(f'`{card.display}`' if i == 0 else '`?`' for i, card in enumerate(self.cards))


class Blackjack(UserView):
    """Implements a game of Blackjack."""

    def __init__(self, ctx: Context, *, bet: int, record: UserRecord) -> None:
        super().__init__(ctx.author, timeout=60)

        self.ctx = ctx
        self.record = record
        self.bet = bet
        self._embed = discord.Embed(color=Colors.secondary, timestamp=ctx.now).set_author(
            name=f'{ctx.author}: Blackjack Game', icon_url=ctx.author.avatar,
        )

        # setup blackjack
        self.deck: Deck[BlackjackCard] = Deck(2, cls=BlackjackCard)  # blackjack is usually played with 1-8 decks
        self.deck.shuffle()
        # draw cards
        self.player: BlackjackHand = BlackjackHand(self.deck.draw_many(2))
        self.dealer: BlackjackHand = BlackjackHand(self.deck.draw_many(2))
        self.doubled_down: bool = False

        if record.wallet < bet * 2:
            self.double_down.disabled = True

    def make_embed(self) -> discord.Embed:
        embed = self._embed.copy()
        embed.clear_fields()

        embed.description = f'**Bet:** {Emojis.coin} {self.bet:,}'
        if self.doubled_down:
            embed.description += '\n**Doubled down:** Your bet has been doubled.'

        embed.add_field(name=f'{self.ctx.author}: **{self.player.total}**', value=self.player.full_display)
        if self.is_finished():
            embed.add_field(name=f'Dealer: **{self.dealer.total}**', value=self.dealer.full_display)
            embed.set_footer(text=f'Next card: {self.deck.cards[-1].display if self.deck.cards else "None"}')
        else:
            embed.add_field(name=f'Dealer', value=self.dealer.hidden_display)

        return embed

    async def handle_lose(self, message: str) -> discord.Embed:
        self.stop()
        embed = self.make_embed()
        embed.colour = Colors.error
        await self.record.add(wallet=-self.bet)

        embed.add_field(
            name=f"**{message}**",
            value=dedent(f'''
                You lost {Emojis.coin} **{self.bet:,} coins**.
                You now have {Emojis.coin} **{self.record.wallet:,}**.
            '''),
            inline=False,
        )
        return embed

    async def lose(self, interaction: TypedInteraction, message: str) -> None:
        embed = await self.handle_lose(message)
        await interaction.response.edit_message(embed=embed, view=None)

    async def handle_win(self, message: str) -> discord.Embed:
        self.stop()
        multiplier = random.uniform(0.7, 1.0)
        adjustment, adjusted_text = Casino.adjust_multiplier(self.record, modification=0.5)
        multiplier += adjustment
        profit = await self.record.add_coins(round(self.bet * multiplier))

        embed = self.make_embed()
        embed.colour = Colors.success
        embed.add_field(
            name=f"**{message}**",
            value=dedent(f"""
                You won {Emojis.coin} **{profit:,} coins**.
                Multiplier: **{multiplier:.1%}**{adjusted_text}
                You now have {Emojis.coin} **{self.record.wallet:,}**.
            """),
            inline=False,
        )
        return embed

    async def win(self, interaction: TypedInteraction, message: str) -> None:
        embed = await self.handle_win(message)
        await interaction.response.edit_message(embed=embed, view=None)

    async def handle_tie(self, message: str) -> discord.Embed:
        self.stop()
        embed = self.make_embed()
        embed.colour = Colors.warning
        embed.add_field(
            name=f"**{message}**",
            value=dedent(f"""
                You neither won nor lost any coins.
                You still have {Emojis.coin} **{self.record.wallet:,}**.
            """),
            inline=False,
        )
        return embed

    async def tie(self, interaction: TypedInteraction, message: str) -> None:
        embed = await self.handle_tie(message)
        await interaction.response.edit_message(embed=embed, view=None)

    async def handle_hit(self, interaction: TypedInteraction) -> None:
        self.player.cards.append(self.deck.draw())
        if self.player.total > 21:
            return await self.lose(interaction, 'Bust! You went over 21!')

        # Automatically stand if the player has 21
        if self.player.total == 21:
            await self.handle_stand(interaction)

    @discord.ui.button(label='Hit', style=discord.ButtonStyle.success)
    async def hit(self, interaction: TypedInteraction, _: discord.ui.Button) -> None:
        await self.handle_hit(interaction)
        if self.is_finished():
            return
        await interaction.response.edit_message(embed=self.make_embed())

    async def handle_stand(self, interaction: TypedInteraction) -> None:
        while self.dealer.total < 17:
            self.dealer.cards.append(self.deck.draw())

        if self.dealer.total > 21:
            return await self.win(interaction, 'Dealer bust!')

        if self.player.total > self.dealer.total:
            return await self.win(interaction, 'You beat the dealer!')
        elif self.player.total == self.dealer.total:
            return await self.tie(interaction, 'You tied with the dealer!')

        return await self.lose(interaction, 'Dealer beat you!')

    @discord.ui.button(label='Stand', style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: TypedInteraction, _: discord.ui.Button) -> None:
        await self.handle_stand(interaction)

    @discord.ui.button(label='Double Down', style=discord.ButtonStyle.primary)
    async def double_down(self, interaction: TypedInteraction, button: discord.ui.Button) -> None:
        self.bet *= 2
        self.doubled_down = True
        button.disabled = True

        await self.handle_hit(interaction)
        if self.is_finished():
            return

        await self.handle_stand(interaction)

    @discord.ui.button(label='Surrender', style=discord.ButtonStyle.danger)
    async def surrender(self, interaction: TypedInteraction, _: discord.ui.Button) -> None:
        await self.lose(interaction, 'You surrendered!')


class Casino(Cog):
    """Gamble off all of your coins at the casino!"""

    emoji = '\U0001f911'

    @staticmethod
    def _format_roll(roll: list[int]) -> str:
        assert len(roll) == 2

        first, second = roll
        return f"{Emojis.dice[first]} {Emojis.dice[second]}"

    @staticmethod
    def adjust_multiplier(record: UserRecord, *, modification: float = 1.0) -> tuple[float, str]:
        expansion = Emojis.Expansion
        if expiry := record.alcohol_expiry:
            multiplier = 0.5 * modification
            return (
                multiplier,
                f'\n{expansion.first} applied +{multiplier:.0%} multiplier from {Items.alcohol.emoji} Alcohol'
                f'\n{expansion.last} expires in {format_dt(expiry, "R")}'
            )
        return 0, ''

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
            multiplier = random.uniform(0.55, 0.95)
            adjustment, adjusted_text = self.adjust_multiplier(record)
            multiplier += adjustment

            profit = await record.add_coins(round(bet * multiplier))

            embed.colour = Colors.success
            embed.set_author(name='Winner!', icon_url=ctx.author.avatar.url)

            embed.add_field(name=f'You won {Emojis.coin} **{profit:,}**!', inline=False, value=dedent(f"""
                Multiplier: **{multiplier:.1%}**{adjusted_text}
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

    @command(aliases={'bj', '21'})
    @simple_cooldown(1, 25)
    @user_max_concurrency(1)
    @lock_transactions
    async def blackjack(self, ctx: Context, *, bet: CasinoBet(500)) -> Any:
        """Bet your coins in a game of Blackjack."""
        record = await ctx.db.get_user_record(ctx.author.id)

        game = Blackjack(ctx, bet=bet, record=record)
        # Check for blackjack
        if game.player.natural:
            if game.dealer.natural:
                yield await game.handle_tie('Standoff'), REPLY
            else:
                yield await game.handle_win('Blackjack!'), REPLY
            return
        elif game.dealer.natural:
            yield await game.handle_lose('Dealer got blackjack!'), REPLY
            return

        yield game.make_embed(), game, REPLY
        await game.wait()


setup = Casino.simple_setup

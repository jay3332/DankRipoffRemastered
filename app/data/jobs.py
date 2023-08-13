from __future__ import annotations

import asyncio
import datetime
import math
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, NamedTuple, TypeAlias, TYPE_CHECKING

import discord

from app.data.items import Item, Items
from app.util.common import insert_random_u200b
from app.util.views import UserView

if TYPE_CHECKING:
    from app.core import Context
    from app.util.types import AsyncCallable, TypedInteraction

    MinigameCallback: TypeAlias = 'AsyncCallable[[Context, discord.Embed, Job], discord.Message | None]'


class MinigameFailure(Exception):
    pass


class Minigame(NamedTuple):
    name: str
    callback: MinigameCallback


def minigame(name: str) -> Callable[[MinigameCallback], Minigame]:
    def decorator(func: MinigameCallback) -> Minigame:
        return Minigame(name, func)

    return decorator


@minigame('Unscramble')
async def unscramble(ctx: Context, embed: discord.Embed, job: Job) -> discord.Message:
    word = random.choice(job.keywords)
    scrambled = ' '.join(''.join(random.sample(word, len(word))) for word in word.split(' '))

    embed.add_field(
        name='Unscramble',
        value=f'Unscramble the following word: **{scrambled}**',
    )
    await ctx.maybe_edit(embed=embed)

    def check(m: discord.Message) -> bool:
        return (
            m.author == ctx.author
            and m.channel == ctx.channel
            and m.content.lower().replace(' ', '') == word.replace(' ', '')
        )

    try:
        response = await ctx.bot.wait_for('message', check=check, timeout=20)
    except TimeoutError:
        raise MinigameFailure(
            f'You didn\'t get the word in time, the correct word was **{word}**. You failed work today.',
        )
    ctx.bot.loop.create_task(ctx.thumbs(response))
    return response



STYLES = {
    discord.ButtonStyle.primary: '<:blurple:1139915753739522201>',
    discord.ButtonStyle.success: '<:green:1139915778628526212>',
    discord.ButtonStyle.danger: '<:red:1139915793937727570>',
}


class LogicComparisonOperator(Enum):
    eq = '='
    ne = '≠'
    lt = '<'
    le = '≤'
    gt = '>'
    ge = '≥'


class LogicConstraint:
    def check(self, button: LogicGameButton) -> bool:
        raise NotImplementedError

    @property
    def display(self) -> str:
        raise NotImplementedError


@dataclass
class LogicNumericConstraint(LogicConstraint):
    value: int
    operator: LogicComparisonOperator

    def check(self, button: LogicGameButton) -> bool:
        label = int(button.label)
        match self.operator:
            case LogicComparisonOperator.eq:
                return label == self.value
            case LogicComparisonOperator.ne:
                return label != self.value
            case LogicComparisonOperator.lt:
                return label < self.value
            case LogicComparisonOperator.le:
                return label <= self.value
            case LogicComparisonOperator.gt:
                return label > self.value
            case LogicComparisonOperator.ge:
                return label >= self.value

    @property
    def display(self) -> str:
        if self.operator is LogicComparisonOperator.eq:
            return str(self.value)
        return f'{self.operator.value} {self.value}'


@dataclass
class LogicColorConstraint(LogicConstraint):
    style: discord.ButtonStyle

    def check(self, button: LogicGameButton) -> bool:
        return button.style == self.style

    @property
    def display(self) -> str:
        return STYLES[self.style]


@dataclass
class LogicNotConstraint(LogicConstraint):
    constraint: LogicConstraint

    def check(self, button: LogicGameButton) -> bool:
        return not self.constraint.check(button)

    @property
    def display(self) -> str:
        return f'(**NOT** {self.constraint.display})'


@dataclass
class LogicAndConstraint(LogicConstraint):
    lhs: LogicConstraint
    rhs: LogicConstraint

    def check(self, button: LogicGameButton) -> bool:
        return self.lhs.check(button) and self.rhs.check(button)

    @property
    def display(self) -> str:
        return f'({self.lhs.display} **AND** {self.rhs.display})'


@dataclass
class LogicOrConstraint(LogicConstraint):
    lhs: LogicConstraint
    rhs: LogicConstraint

    def check(self, button: LogicGameButton) -> bool:
        return self.lhs.check(button) or self.rhs.check(button)

    @property
    def display(self) -> str:
        return f'({self.lhs.display} **OR** {self.rhs.display})'


class LogicGameButton(discord.ui.Button['LogicGameView']):
    async def callback(self, interaction: TypedInteraction) -> Any:
        self.disabled = True
        await interaction.response.edit_message(view=self.view)


def generate_random_atom_constraint() -> LogicConstraint:
    color_constraint = LogicColorConstraint(random.choice(list(STYLES)))
    numeric_constraint = LogicNumericConstraint(
        random.randint(1, 3),
        random.choice(list(LogicComparisonOperator)),
    )

    match random.randint(0, 2):
        case 0:
            return color_constraint
        case 1:
            return numeric_constraint
        case 2:
            return LogicNotConstraint(random.choice([color_constraint, numeric_constraint]))


def generate_random_constraint() -> LogicConstraint:
    first = generate_random_atom_constraint()
    match random.randint(0, 2):
        case 0:
            return LogicNotConstraint(first)
        case 1:
            return LogicAndConstraint(first, generate_random_atom_constraint())
        case 2:
            return LogicOrConstraint(first, generate_random_atom_constraint())


class LogicGameView(UserView):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx.author, timeout=60)
        for i in range(9):
            style = random.choice(list(STYLES))
            self.add_item(LogicGameButton(style=style, label=random.choice('123'), row=i // 3))

        self.ctx = ctx
        self.constraint: LogicConstraint = generate_random_constraint()
        self.failed: bool = False
        self.message: str = "buddy wya you failed work today because you're slow"

    @discord.ui.button(label='Submit', row=3)
    async def submit(self, interaction: TypedInteraction, _) -> None:
        for child in self.children:
            if not isinstance(child, LogicGameButton):
                child._correct = True
                continue
            correct = self.constraint.check(child)  # type: ignore
            child._correct = child.disabled and correct or not child.disabled and not correct
            child.disabled = not correct

        if any(not child._correct for child in self.children):
            self.failed = True
            self.message = 'You got it wrong and you failed work today (the correct buttons are displayed above)'

        self.stop()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label='Clear', row=3)
    async def clear(self, interaction: TypedInteraction, _) -> None:
        for child in self.children:
            child.disabled = False
        await interaction.response.edit_message(view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if not isinstance(child, LogicGameButton):
                continue
            child.disabled = not self.constraint.check(child)  # type: ignore

        self.failed = True
        await self.ctx.maybe_edit(message=self.ctx._message, view=self)


@minigame('Logic Game')
async def logic_game(ctx: Context, embed: discord.Embed, _job: Job) -> discord.Message:
    view = LogicGameView(ctx)
    embed.add_field(
        name=f'Logic Game!',
        value=(
            'Click the buttons that match this condition: '
            + view.constraint.display.removeprefix('(').removesuffix(')')
            + '\n*Selected buttons will appear as disabled*'
        ),
    )
    message = await ctx.maybe_edit(embed=embed, view=view)
    await view.wait()
    if view.failed:
        raise MinigameFailure(view.message)
    return message


@minigame('Retype')
async def retype(ctx: Context, embed: discord.Embed, job: Job) -> discord.Message:
    phrase = random.choice(job.phrases)
    embed.add_field(name='Retype', value=f'Retype the following sentence fast:\n**{insert_random_u200b(phrase)}**')
    await ctx.maybe_edit(embed=embed)

    def check(m: discord.Message) -> bool:
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        response = await ctx.bot.wait_for('message', check=check, timeout=12)
    except asyncio.TimeoutError:
        raise MinigameFailure("you're too slow, you failed work today. try again next time buddy!")

    if '\u200b' in response.content:
        raise MinigameFailure('cheater, you failed work today. try again next time buddy!')
    if response.content.lower() != phrase.lower():
        raise MinigameFailure("You didn't type the phrase properly, so you failed work today.")
    return response


class TicTacToeButton(discord.ui.Button['TicTacToe']):
    def __init__(self, index: int, row: int) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label='\u200b', row=row)
        self.index = index

    async def callback(self, interaction: TypedInteraction) -> None:
        board = self.view.board
        if board[self.index] != self.view.EMPTY:
            return await interaction.response.send_message(
                'Someone already played in that cell, pick somewhere else', ephemeral=True,
            )

        self.view.board[self.index] = self.view.X
        self.view.update()
        await interaction.response.edit_message(view=self.view)


class TicTacToe(UserView):
    EMPTY = 0
    X = 1
    O = 2
    TIE = 3

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx.author, timeout=60)
        self.ctx = ctx
        self.board = [0, 0, 0, 0, 0, 0, 0, 0, 0]

        for i in range(9):
            self.add_item(TicTacToeButton(index=i, row=i // 3))

        self.winner = self.EMPTY

    def update(self) -> None:
        winner, cells = self.get_winner()
        if winner == self.EMPTY:
            self.board[self.get_best_move()] = self.O  # compute move
            winner, cells = self.get_winner()
        self.winner = winner

        for i, child in enumerate(self.children):
            if self.board[i] == self.X:
                child.style = discord.ButtonStyle.primary
                child.label = 'X'
            elif self.board[i] == self.O:
                child.style = discord.ButtonStyle.danger
                child.label = 'O'

        if winner != self.EMPTY:
            for i, child in enumerate(self.children):
                if cells is None or i not in cells:
                    child.disabled = True
            self.stop()

    def get_winner(self) -> tuple[int, tuple[int, int, int] | None]:
        board = self.board
        # check horizontal and vertical
        for i in range(3):
            if board[i] == board[i + 3] == board[i + 6] != self.EMPTY:
                return board[i], (i, i + 3, i + 6)

            offset = i * 3
            if board[offset] == board[offset + 1] == board[offset + 2] != self.EMPTY:
                return board[i * 3], (offset, offset + 1, offset + 2)

        # check diagonals
        if board[0] == board[4] == board[8] != self.EMPTY:
            return board[0], (0, 4, 8)
        if board[2] == board[4] == board[6] != self.EMPTY:
            return board[2], (2, 4, 6)
        # check tie
        if all(i != self.EMPTY for i in board):
            return self.TIE, None
        # no winner
        return self.EMPTY, None

    @property
    def empty_cells(self) -> list[int]:
        return [i for i, cell in enumerate(self.board) if cell == self.EMPTY]

    @classmethod
    def get_bias(cls, winner: int) -> int:
        if winner == cls.X:
            return -1
        elif winner == cls.O:
            return 1
        return 0

    def minimax(self, depth: int, is_maximizing: bool) -> int:
        depth += 1
        winner, _ = self.get_winner()
        if winner != self.EMPTY:
            return self.get_bias(winner) * depth

        best_score = -math.inf if is_maximizing else math.inf
        player = self.O if is_maximizing else self.X
        f = max if is_maximizing else min

        for cell in self.empty_cells:
            self.board[cell] = player
            score = self.minimax(depth, not is_maximizing)
            self.board[cell] = self.EMPTY
            best_score = f(score, best_score)
        return best_score

    def get_best_move(self) -> int:
        best_score = -math.inf
        best_move = None
        for cell in self.empty_cells:
            self.board[cell] = self.O
            score = self.minimax(0, False)
            self.board[cell] = self.EMPTY
            if score > best_score:
                best_score = score
                best_move = cell
        return best_move


@minigame('Tic-Tac-Toe')
async def tic_tac_toe(ctx: Context, embed: discord.Embed, _job: Job) -> discord.Message:
    game = TicTacToe(ctx)
    embed.add_field(
        name='Tic-Tac-Toe',
        value=(
            'You are playing as X, beat or tie the bot to win!\n'
            '*Fun fact, you can\'t actually win, so just try to draw*'
        ),
    )

    message = await ctx.maybe_edit(embed=embed, view=game)
    await game.wait()

    if game.winner == game.O:
        raise MinigameFailure("didn't see that one coming, huh? You failed work for today.")

    return message


class Job(NamedTuple):
    name: str
    key: str
    description: str
    emoji: str
    keywords: list[str]
    phrases: list[str]
    minigames: list[Minigame]
    base_salary: int
    cooldown: datetime.timedelta
    items: dict[Item | None, float]
    work_experience_required: int = 0
    intelligence_required: int = 0
    singular: str = None

    @property
    def actual_singular(self) -> str:
        return self.singular or (
            'an' if self.name[0].lower() in 'aeiou' else 'a'
        )

    @property
    def chunk(self) -> str:
        return f'{self.actual_singular} **{self.name}**'

    @property
    def chunk_display(self) -> str:
        return f'{self.actual_singular} {self.emoji} **{self.name}**'

    @property
    def display(self) -> str:
        return f'{self.emoji} {self.name}'

    def __repr__(self) -> str:
        return f'<Job name={self.name!r} key={self.key!r}>'


class Jobs:
    """Class to hold job data."""

    discord_mod = Job(
        name='Discord Mod',
        key='discord_mod',
        description='Moderate a Discord server and stay away from grass',
        emoji='\U0001fae1',
        keywords=[
            'discord',
            'moderator',
            'chat',
            'server',
            'mod',
            'kick',
            'ban',
            'warn',
            'mute',
            'kitten',
            'discord kitten',
            'avoid grass',
        ],
        phrases=[
            'no memes in general',
            "now who's telling me to touch grass?",
            'can we get a mute on this guy?',
            'come here my little discord kitten',
            'my mother keeps telling me to get a real job',
            'shut up and follow the rules',
            'rule 1: no disrespecting admins',
            'shower? never heard of it',
        ],
        minigames=[unscramble, retype, tic_tac_toe],
        base_salary=750,
        cooldown=datetime.timedelta(minutes=30),
        work_experience_required=0,
        items={
            None: 1,
            Items.ban_hammer: 0.1,
            Items.alcohol: 0.02,
        },
    )

    youtuber = Job(
        name='Youtuber',
        key='youtuber',
        description='Make videos on YouTube and get paid for it',
        emoji='\U0001f3ac',
        keywords=[
            'youtube',
            'youtuber',
            'video',
            'content',
            'content creator',
            'subscribe',
            'like',
            'like and subscribe',
            'comment',
            'adsense',
            'monetize',
            'demonetized',
        ],
        phrases=[
            'make sure to like and subscribe',
            'make sure to hit that notification bell',
            'comment down below what you want to see next',
            'hey guys, welcome back to another video',
            'be sure to check out my other videos',
            'i got demonetized again',
            'hey guys, welcome back to another vlog',
        ],
        minigames=[unscramble, retype, tic_tac_toe, logic_game],
        base_salary=800,
        cooldown=datetime.timedelta(minutes=30),
        work_experience_required=0,
        items={
            None: 1,
            Items.camera: 0.02,
        },
    )

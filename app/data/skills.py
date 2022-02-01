from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING, TypeAlias

import discord

from app.util.common import insert_random_u200b
from app.util.views import AnyUser, UserView
from config import Colors

if TYPE_CHECKING:
    from app.core import Context

    BenefitFmt: TypeAlias = Callable[[int], str]
    TrainingCallback: TypeAlias = 'Callable[[Skills, Context, Skill], Awaitable[Any]]'
    LevelRequirementMapping: TypeAlias = 'dict[int, int]'  # point_count: level_required


DEFAULT_LEVEL_REQUIREMENT_MAPPING = {
    5: 0,
    15: 10,
    35: 25,
    75: 50,
}


@dataclass
class Skill:
    """Stores data about skill area."""
    key: str
    name: str
    description: str
    benefit: str | BenefitFmt
    price: int

    level_unlocked: int = 0
    level_requirement_mapping: LevelRequirementMapping = field(default_factory=lambda: DEFAULT_LEVEL_REQUIREMENT_MAPPING)
    max_points: int | None = None

    training_cooldown: int = 600  # 10 minutes
    training_callback: TrainingCallback = None

    def __post_init__(self) -> None:
        if isinstance(self.benefit, str):
            self.benefit = lambda _: self.benefit

    def __hash__(self) -> int:
        return hash(self.key)

    def __str__(self) -> str:
        return self.key

    @property
    def benefit_per_point(self) -> str:
        return self.benefit(1)

    def to_train(self, func: TrainingCallback) -> TrainingCallback:
        self.training_callback = func

        return func

    def run_training(self, ctx: Context) -> Awaitable[Any]:
        if self.training_callback is None:
            raise RuntimeError('No training callback given.')

        return self.training_callback(SKILLS_INSTANCE, ctx, self)


class TrainingFailure(Exception):
    pass


class RobberyTrainingButton(discord.ui.Button['RobberyTrainingView']):
    def __init__(self, digit: int, *, row: int | None = None, user: AnyUser | None = None) -> None:
        super().__init__(label=str(digit), style=discord.ButtonStyle.primary, row=row)

        self.digit: int = digit
        self._user: AnyUser | None = user

    async def callback(self, interaction: discord.Interaction) -> None:
        if self._user and interaction.user != self._user:
            return await interaction.response.send_message('Nope', ephemeral=True)

        self.view.entered += str(self.digit)
        self.view.update()

        await interaction.response.edit_message(embed=self.view.embed, view=self.view)


class RobberyTrainingView(UserView):
    def __init__(self, ctx: Context, embed: discord.Embed, code: int) -> None:
        super().__init__(ctx.author)

        self.code: int = code
        self.embed: discord.Embed = embed
        self.entered: str = ''

        self.dangling_interaction: discord.Interaction | None = None

        self.clear_button = discord.ui.Button(label='Clear', style=discord.ButtonStyle.danger)
        self.submit_button = discord.ui.Button(label='Submit', style=discord.ButtonStyle.success)

        self.clear_button.callback = self.clear_callback
        self.submit_button.callback = self.submit_callback

        self.scramble_buttons()

    def update(self) -> None:
        self.embed.remove_field(1)

        if self.entered:
            self.embed.add_field(name='\u200b', value=f'```py\n{self.entered}```', inline=False)

        self.scramble_buttons()

    def scramble_buttons(self) -> None:
        buttons = [RobberyTrainingButton(i) for i in range(10)]
        random.shuffle(buttons)

        self.clear_items()
        for button in buttons:
            self.add_item(button)

        self.add_item(self.clear_button)
        self.add_item(self.submit_button)

    async def clear_callback(self, interaction: discord.Interaction) -> None:
        self.entered = ''
        self.update()

        await interaction.response.edit_message(embed=self.embed, view=self)

    async def submit_callback(self, interaction: discord.Interaction) -> None:
        self.dangling_interaction = interaction
        self.stop()


PUNCH = 'Punch'
KICK = 'Kick'
LOW_PUNCH = 'Low Punch'
HIGH_KICK = 'High Kick'

JUMP = 'Jump!', '\u23eb'
DUCK = 'Duck!', '\u23ec'
BLOCK = 'Block!', '\U0001f6e1'


class DefenseTrainingButton(discord.ui.Button['DefenseTrainingView']):
    def __init__(self, action: tuple[str, str]) -> None:
        self.action, emoji = action

        super().__init__(label=self.action, emoji=emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.choice = self.action

        match self.view.action, self.action:
            case 'Punch', 'Duck!':
                self.view._is_correct = True
            case 'Kick', 'Jump!':
                self.view._is_correct = True
            case 'Low Punch' | 'High Kick', 'Block!':
                self.view._is_correct = True
            case _:
                self.view._is_correct = False

        for button in self.view.children:
            assert isinstance(button, discord.ui.Button)

            if button.label == self.action:
                button.style = discord.ButtonStyle.success if self.view._is_correct else discord.ButtonStyle.danger

            button.disabled = True

        await interaction.response.edit_message(view=self.view)
        self.view.stop()


class DefenseTrainingView(UserView):
    def __init__(self, ctx: Context, action: str) -> None:
        self.ctx: Context = ctx
        self.action: str = action

        super().__init__(ctx.author, timeout=5)
        self.choice: str | None = None
        self._is_correct: bool | None = None

        self.add_item(DefenseTrainingButton(JUMP))
        self.add_item(DefenseTrainingButton(DUCK))
        self.add_item(DefenseTrainingButton(BLOCK))


class Skills:
    """Stores all of the skills."""
    begging = Skill(
        key='begging',
        name='Begging',
        description='Improving this skill will increase the chance and coins gained from the `beg` command.',
        benefit=lambda p: f'+{p / 2}% chance to get items from begging, +{p * 2}% coins from begging',
        price=2500,
        max_points=100,
    )

    TRAIN_BEGGING_PROMPTS = (
        'please spare me some change',
        'i need some coins',
        'may i have some free coins?',
        'foolish you, i need some coins.',
        'i need some spare change, thanks',
        'give me coins, please',
        'spare me some change, would you?',
        'money give me',
    )

    @begging.to_train
    async def train_begging(self, ctx: Context, _: Skill) -> Any:
        prompts = random.sample(self.TRAIN_BEGGING_PROMPTS, k=3)

        for i, prompt in enumerate(prompts, start=1):
            embed = discord.Embed(color=Colors.primary)

            embed.add_field(name='Type the following into chat:', value=f'**`{insert_random_u200b(prompt)}`**')
            embed.set_author(name=f'{ctx.author.name}: Training Begging Skill', icon_url=ctx.author.avatar.url)
            embed.set_footer(text=f'Prompt {i} out of 3')

            await ctx.send(embed=embed)

            try:
                message = await ctx.bot.wait_for(
                    event='message',
                    check=lambda m: m.author == ctx.author and m.content.lower() == prompt,
                    timeout=15,
                )
            except asyncio.TimeoutError:
                raise TrainingFailure("You didn't send the prompt in time, and you failed training for this session. Try again next time!")

            ctx.bot.loop.create_task(ctx.thumbs(message))

    _common_requirement_mapping = {
        5: 0,
        15: 10,
        35: 25,
        50: 35,
    }

    robbery = Skill(
        key='robbery',
        name='Robbery',
        description='Improve your chances of success along with net gain when using the `rob` command. (Max. +50% payouts)',
        benefit=lambda p: f'+{p}% success chance, -{p / 2}% death chance, +{min(p * 2, 50)}% payouts',
        price=8000,
        training_cooldown=1800,
        max_points=50,
        level_requirement_mapping=_common_requirement_mapping,
    )

    @robbery.to_train
    async def train_robbery(self, ctx: Context, _: Skill) -> Any:
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name}: Training Robbery Skill', icon_url=ctx.author.avatar.url)

        embed.description = (
            "Practice entering this combination into the keypad before time runs out!\n"
            "Because this is Discord and I can do whatever I want, I made the keypad randomize each time."
        )

        code = random.randint(10000000, 99999999)
        embed.add_field(name='Enter the following combination:', value=code)

        view = RobberyTrainingView(ctx, embed, code)
        original = await ctx.send(embed=embed, view=view)

        try:
            await asyncio.wait_for(view.wait(), timeout=20)
        except asyncio.TimeoutError:
            raise TrainingFailure("You didn't enter the code in time and the keypad disappears. Try again next time!")

        if view.entered != str(code):
            raise TrainingFailure("You didn't enter the code correctly and the keypad disappears. Try again next time!")

        embed.colour = Colors.success
        await ctx.maybe_edit(original, embed=embed)

    defense = Skill(
        key='defense',
        name='Defense',
        description='Lowers the success chance of others trying to rob you.',
        benefit=lambda p: f'-{p * 1.5}% rob success chance, +{p / 2}% death chance for others',
        price=8000,
        training_cooldown=1800,
        max_points=50,
        level_requirement_mapping=_common_requirement_mapping,
    )

    @defense.to_train
    async def train_defense(self, ctx: Context, _: Skill) -> Any:
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name}: Training Defense Skill', icon_url=ctx.author.avatar.url)
        embed.set_footer(text='Starting in 5 seconds.')

        embed.description = (
            'A dummy opponent will be training your defense skills by trying to hit you.\n'
            'When the opponent is about to punch, you must **duck**. When they are about to kick, you must **jump**.\n\n'
            'Additionally, when the opponent tries dealing a __low punch__ or a __high kick__, you must **block**.'
        )

        await ctx.send(embed=embed, reference=ctx.message)
        await asyncio.sleep(5)

        for i in range(1, 6):
            action = random.choice((PUNCH, KICK, LOW_PUNCH, HIGH_KICK))
            view = DefenseTrainingView(ctx, action)

            await ctx.send(
                f'[{i}/5] Dummy opponent is about to deal a **{action}** - what will you do?', reference=ctx.message, view=view,
            )

            await view.wait()

            if view.choice is None:
                raise TrainingFailure("You didn't respond in time and the dummy opponent beats you. Try again next time!")

            elif not view._is_correct:
                raise TrainingFailure("Wrong choice! The dummy opponent beats you. Try again next time!")


SKILLS_INSTANCE = Skills()

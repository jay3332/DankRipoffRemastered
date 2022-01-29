from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeAlias, TYPE_CHECKING

import discord

from app.util.common import insert_random_u200b
from config import Colors

if TYPE_CHECKING:
    from app.core import Context

    BenefitFmt: TypeAlias = Callable[[int], str]
    TrainingCallback: TypeAlias = 'Callable[[Skills, Context, Skill], Awaitable[Any]]'
    LevelRequirementMapping: TypeAlias = 'dict[int, int]'  # point_count: level_required


DEFAULT_LEVEL_REQUIREMENT_MAPPING = {
    5: 3,
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


SKILLS_INSTANCE = Skills()

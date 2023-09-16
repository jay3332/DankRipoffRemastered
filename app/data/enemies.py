from __future__ import annotations

from typing import NamedTuple

from app.data.abilities import Ability, Abilities
from app.data.items import Items


class Enemy(NamedTuple):
    key: str
    name: str
    description: str
    emoji: str
    base_hp: int
    abilities: dict[Ability, float]
    curve: float = 1.22
    spawn_in_events: bool = False

    @property
    def display(self) -> str:
        return f'{self.emoji} {self.name}'

    def hp_at_level(self, level: int, /) -> int:
        return round(self.base_hp * (self.curve ** (level - 1)))

    def __hash__(self) -> int:
        return hash(self.key)

    def __str__(self) -> str:
        return self.name


class Enemies:
    """A collection of all enemies."""

    karen = Enemy(
        key='karen',
        name='Karen',
        description='Yet another Karen throwing a fit in public. Where\'s the manager?',
        emoji='<:karen:1141919669536694324>',
        base_hp=100,
        abilities={
            Abilities.punch: 1.0,
            Abilities.speak_to_the_manager: 0.25,
            Abilities.insult: 0.25,
        },
        spawn_in_events=True,
    )

    cop = Enemy(
        key='cop',
        name='Cop',
        description="A cop who's had a bad day. He'll be there to arrest you if you don't watch out.",
        emoji='<:cop:1142668034373341268>',
        base_hp=100,
        abilities={
            Abilities.punch: 1.0,
            Abilities.taser: 0.3,
            Abilities.handcuffs: 0.3,
        },
    )

    shark = Enemy(
        key='shark',
        name='Shark',
        description=Items.shark.description,
        emoji=Items.shark.emoji,
        base_hp=60,
        abilities={
            Abilities.swim: 0.8,
            Abilities.serrated_fins: 0.4,
            Abilities.shark_bite: 0.2,
        }
    )

from enum import Enum
from typing import Callable, NamedTuple


class PetRarity(Enum):
    common = 0
    uncommon = 1
    rare = 2
    epic = 3
    legendary = 4
    mythic = 5
    special = 6


class Pet(NamedTuple):
    name: str
    key: str
    emoji: str
    rarity: PetRarity
    description: str
    energy_per_minute: int
    max_energy: int
    benefit: Callable[[int], str]  # Passive
    abilities: Callable[[int], str] | None = None  # Active
    # Leveling
    leveling_curve: tuple[int, int] = (50, 1.15)
    max_level: int = 200

    @property
    def display(self) -> str:
        return f'{self.emoji} {self.name}'

    def full_abilities(self, level: int) -> str:
        if self.abilities is None:
            return self.benefit(level)
        return f'{self.benefit(level)}\n{self.abilities(level)}'

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return self.key == other.key


class Pets:
    dog = Pet(
        name='Dog',
        key='dog',
        emoji='<:dog:1134641292245205123>',
        rarity=PetRarity.common,
        description="A descendant of the wolf and a man's best friend.",
        energy_per_minute=1,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.3}% coins from begging\n'
            f'- +{min(1 + level * 0.4, 50)}% chance to find items while searching'
        ),
    )

    cat = Pet(
        name='Cat',
        key='cat',
        emoji='<:cat:1134641341092089948>',
        rarity=PetRarity.common,
        description='A small, domesticated, carnivorous mammal.',
        energy_per_minute=1,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.2}% weight on finding rarer items when fishing\n'
            f'- +{1 + level * 0.3}% global XP multiplier'
        ),
    )

    bird = Pet(
        name='Bird',
        key='bird',
        emoji='\U0001f426',
        rarity=PetRarity.common,
        description='Birb. These can fly, in case you were clueless.',
        energy_per_minute=1,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.4}% global coin multiplier'
        ),
    )

    bee = Pet(
        name='Bee',
        key='bee',
        emoji='\U0001f41d',
        rarity=PetRarity.uncommon,
        description='A flying insect that pollinates flowers and makes honey.',
        energy_per_minute=2,
        max_energy=300,
        benefit=lambda level: (
            f'- +{1 + level * 0.4}% faster harvesting crops\n'
            f'- {2 + level * 0.25}% chance to sting someone when they try robbing you'
        ),
        abilities=lambda level: (
            f'- Produce honey (1 per hour) with `.honey`'
        )
    )

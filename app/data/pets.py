from enum import Enum
from typing import Callable, NamedTuple

from config import Emojis


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
    energy_per_minute: float
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
        energy_per_minute=0.5,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.3}% coins from begging\n'
            f'- +{1 + level * 0.4}% chance to find items while searching'
        ),
    )

    cat = Pet(
        name='Cat',
        key='cat',
        emoji='<:cat:1134641341092089948>',
        rarity=PetRarity.common,
        description='A small, domesticated, carnivorous mammal.',
        energy_per_minute=0.5,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.2}% weight on finding rarer items when fishing\n'
            f'- +{0.8 + level * 0.4}% global XP multiplier'
        ),
    )

    bird = Pet(
        name='Bird',
        key='bird',
        emoji='\U0001f426',
        rarity=PetRarity.common,
        description='Birb. These can fly, in case you were clueless.',
        energy_per_minute=0.5,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.4}% global coin multiplier'
        ),
    )

    bunny = Pet(
        name='Bunny',
        key='bunny',
        emoji='\U0001f430',
        rarity=PetRarity.common,
        description='A mammal with long ears that hops around.',
        energy_per_minute=0.5,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.5}% global XP multiplier'
        ),
    )

    hamster = Pet(
        name='Hamster',
        key='hamster',
        emoji='\U0001f439',
        rarity=PetRarity.common,
        description='A small rodent that is often kept as a pet.',
        energy_per_minute=0.5,
        max_energy=100,
        benefit=lambda level: (
            f'- +{1 + level * 0.4}% weight on finding rarer items when digging\n'
            f'- +{0.5 + level * 0.1}% money back when buying items'
        ),
    )

    mouse = Pet(
        name='Mouse',
        key='mouse',
        emoji='\U0001f42d',
        rarity=PetRarity.common,
        description='A small rodent that likes cheese.',
        energy_per_minute=1,
        max_energy=100,
        benefit=lambda level: (
            f'- +{5 + level * 0.5}% XP multiplier increase from eating cheese\n'
            f'- +{1 + level * 0.4}% chance to find items while searching'
        ),
    )

    duck = Pet(
        name='Duck',
        key='duck',
        emoji='\U0001f986',
        rarity=PetRarity.uncommon,
        description='Waddle waddle and then they go quack',
        energy_per_minute=0.75,
        max_energy=200,
        benefit=lambda level: (
            f'- +{2 + level * 0.5}% profit from working\n'  # TODO
            f'- +{1 + level * 0.25}% chance to get an Uncommon Crate when claiming hourly crates\n'  # TODO
            f'- +{1 + level * 0.3}% global XP multiplier'
        ),
    )

    bee = Pet(
        name='Bee',
        key='bee',
        emoji='\U0001f41d',
        rarity=PetRarity.uncommon,
        description='A flying insect that pollinates flowers and makes honey.',
        energy_per_minute=0.75,
        max_energy=300,
        benefit=lambda level: (
            f'- +{1 + level * 0.4}% faster harvesting crops\n'
            f'- {2 + level * 0.25}% chance to sting someone when they try robbing you'
        ),
        abilities=lambda level: (
            f'- Produce honey (1 per hour) with `.honey` ({Emojis.bolt} 60)'
        )
    )

    cow = Pet(
        name='Cow',
        key='cow',
        emoji='\U0001f42e',
        rarity=PetRarity.rare,
        description='A large mammal used for producing milk (and steak of course).',
        energy_per_minute=1.25,
        max_energy=500,
        benefit=lambda level: (
            f'- +{2 + level * 0.5}% more coins from beg, search, and crime\n'
            f'- +{2 + level * 0.6}% global XP multiplier\n'
        ),
        abilities=lambda level: (
            f'- Produce milk (1 per hour) with `.milk` ({Emojis.bolt} 100)'
        )
    )

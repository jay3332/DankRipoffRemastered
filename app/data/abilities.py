from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, NamedTuple, TypeAlias, TYPE_CHECKING

from app.core.helpers import MISSING
from app.util.common import get_by_key

if TYPE_CHECKING:
    from app.data.enemies import Enemy
    from app.data.pets import Pet
    from app.features.battles import BattleContext
    from app.util.types import AsyncCallable

    AbilityCallback: TypeAlias = AsyncCallable[['Abilities', BattleContext], Any]


class RefType(Enum):
    enemy = 0
    pet = 1


class Ref(NamedTuple):
    key: str
    type: RefType


class AbilityType(Enum):
    attack = 'Attack'
    defense = 'Defense'
    healing = 'Healing'
    buff = 'Buff'


@dataclass
class Ability:
    key: str
    name: str
    type: AbilityType
    description: str
    effect: str
    emoji: str
    stamina: int = 0
    curve: tuple[int, int] = (100, 1.22)
    exclusive_to: list[Ref] | None = None
    _callback: AbilityCallback = MISSING

    @property
    def display(self) -> str:
        return f'{self.emoji} {self.name}'

    @property
    def exclusive_to_resolved(self) -> list[Enemy | Pet] | None:
        from app.data.enemies import Enemies
        from app.data.pets import Pets

        if self.exclusive_to is None:
            return None

        return [
            get_by_key(Enemies, ref.key) if ref.type is RefType.enemy else get_by_key(Pets, ref.key)
            for ref in self.exclusive_to
        ]

    def callback(self, func: AbilityCallback) -> AbilityCallback:
        self._callback = func
        return func

    async def dispatch(self, ctx: BattleContext) -> Any:
        return await self._callback(_ABILITIES_INST, ctx)

    def __hash__(self) -> int:
        return hash(self.key)


class Abilities:
    """A collection of all abilities."""

    punch = Ability(
        key='punch',
        name='Punch',
        type=AbilityType.attack,
        description='Punches the opponent somewhere in the body.',
        effect='Deals damage to the opponent.',
        emoji='\U0001f44a',
        stamina=1,
    )

    @punch.callback
    async def callback(self, ctx: BattleContext) -> Any:
        if random.random() < 0.4 * ctx.player.accuracy:
            reason = random.choice((
                'misses!',
                f'{ctx.target.user} dodges it',
                f'{ctx.target.user} blocks it',
            ))
            return ctx.add_attack_commentary(
                text=f'{ctx.player.user} tries to **punch** {ctx.target.user}, but {reason}',
            )

        base = random.uniform(5, 10) * ctx.level ** 1.2
        area = random.choice((
            'face',
            'chest',
            'stomach',
            'arm',
            'leg',
        ))
        if critical := random.random() < 0.1:
            base *= 3

        damage = ctx.deal_attack(round(base))
        ctx.add_attack_commentary(
            damage=damage,
            text=(
                f'{ctx.player.user} **punches** {ctx.target.user} with a mighty blow and deals **{damage} HP!**'
                if critical
                else f'{ctx.player.user} **punches** {ctx.target.user} in the {area} and deals **{damage} HP**'
            ),
            critical=critical,
        )

    kick = Ability(
        key='kick',
        name='Kick',
        type=AbilityType.attack,
        description='Deals a mighty kick the opponent.',
        effect='Deals damage to the opponent.',
        emoji='\U0001f45f',
        stamina=1,
    )

    @kick.callback
    async def callback(self, ctx: BattleContext) -> Any:
        if random.random() < 0.4 * ctx.player.accuracy:
            reason = random.choice((
                'misses!',
                f'{ctx.target.user} dodges it',
                f'{ctx.target.user} blocks it',
            ))
            return ctx.add_attack_commentary(
                text=f'{ctx.player.user} tries to **kick** {ctx.target.user}, but {reason}',
            )

        base = random.uniform(3, 12) * ctx.level ** 1.2
        area = random.choice((
            'stomach',
            'arm',
            'leg',
        ))
        if critical := random.random() < 0.1:
            area = '**groin**'
            base *= 3

        damage = ctx.deal_attack(round(base))
        ctx.add_attack_commentary(
            damage=damage,
            text=f'{ctx.player.user} **kicks** {ctx.target.user} in the {area} and deals **{damage} HP**',
            critical=critical,
        )

    herb = Ability(
        key='herb',
        name='Herb',
        type=AbilityType.healing,
        description='Uses a herb to replenish HP.',
        effect='Heals the player by a small amount of HP.',
        emoji='\U0001f33f',
        stamina=1,
    )

    @herb.callback
    async def callback(self, ctx: BattleContext) -> Any:
        hp = round(random.uniform(6, 12) * ctx.level ** 1.2)
        hp = ctx.player.heal(hp)
        ctx.player.tick_offensive(AbilityType.healing)

        if not hp:
            return ctx.add_heal_commentary(
                text=f'{ctx.player.user} uses a **herb**, but they are already at max HP', heal=0,
            )
        ctx.add_heal_commentary(text=f'{ctx.player.user} uses a **herb** and replenishes **{hp} HP**', heal=hp)

    speak_to_the_manager = Ability(
        key='speak_to_the_manager',
        name='Speak to the Manager',
        type=AbilityType.attack,
        description='Karen demands to speak to the manager, distracting the player and lowering their accuracy.',
        effect='Applies a 50% accuracy debuff for the next 2 attacks from the player.',
        emoji='<:karen:1141919669536694324>',
        exclusive_to=[Ref('karen', RefType.enemy)],
    )

    @speak_to_the_manager.callback
    async def callback(self, ctx: BattleContext) -> Any:
        ctx.target.accuracy_stack.append(0.5, 2, types={AbilityType.attack})
        ctx.add_buff_commentary(
            player=ctx.player,
            text=f'**Karen** demands to speak to the manager, distracting {ctx.target.user}.',
            buff=f'**Accuracy Debuff:** -50% for 2 attacks',
        )

    insult = Ability(
        key='insult',
        name='Insult',
        type=AbilityType.attack,
        description='Insults the opponent, lowering their motivation and lowering their attack.',
        effect='Applies a 50% attack debuff for the next 2 attacks from the opponent.',
        emoji='\U0001f595',
    )

    @insult.callback
    async def callback(self, ctx: BattleContext) -> Any:
        ctx.target.attack_stack.append(0.5, 2, types={AbilityType.attack})
        ctx.add_buff_commentary(
            player=ctx.player,
            text=f'{ctx.player.user} **insults** {ctx.target.user}, lowering their motivation.',
            buff=f'**Attack Debuff:** -50% for 2 attacks',
        )

    block = Ability(
        key='block',
        name='Block',
        type=AbilityType.defense,
        description='Blocks the next attack from the opponent, reducing the damage taken.',
        effect='Reduces the damage taken from the next attack.',
        emoji='\U0001f6e1',
    )

    _BLOCK_BUFF_CURVE_BASE = 1 / 1.025

    @block.callback
    async def callback(self, ctx: BattleContext) -> Any:
        buff = 0.5 * self._BLOCK_BUFF_CURVE_BASE ** (ctx.level - 1)
        ctx.player.defense_stack.append(buff, 1, types={AbilityType.attack})
        ctx.player.tick_offensive(AbilityType.defense)
        ctx.add_buff_commentary(
            player=ctx.player,
            text=f'{ctx.player.user} **blocks** the next attack from {ctx.target.user}.',
            buff=f'**Next attack taken:** -{1 - buff:.1%} damage',
        )

    handcuffs = Ability(
        key='handcuffs',
        name='Handcuffs',
        type=AbilityType.attack,
        description='Handcuffs the opponent, preventing them from attacking for the next turn.',
        effect='Prevents the opponent from attacking for the next turn.',
        emoji='<:handcuffs:1142670902199341056>',
    )

    @handcuffs.callback
    async def callback(self, ctx: BattleContext) -> Any:
        ctx.target.attack_stack.append(0, 1)
        ctx.add_buff_commentary(
            player=ctx.player,
            text=f'{ctx.player.user} **handcuffs** {ctx.target.user}.',
            buff='**Next move:** ATK suppressed to zero',
        )

    taser = Ability(
        key='taser',
        name='Taser',
        type=AbilityType.attack,
        description='Tases the opponent, dealing damage and lowering their defense.',
        effect='Deals a small amount of damage and applies a 50% defense debuff for the next 2 moves from the opponent.',
        emoji='<:taser:1142672274487529473>',
    )

    @taser.callback
    async def callback(self, ctx: BattleContext) -> Any:
        damage = ctx.deal_attack(round(random.uniform(4, 9) * ctx.level ** 1.2))
        ctx.target.defense_stack.append(1.5, 2, types={AbilityType.attack})
        ctx.player.tick_offensive(AbilityType.attack)

        ctx.add_attack_commentary(
            damage=damage,
            text=f'{ctx.player.user} **tases** {ctx.target.user} and deals **{damage} HP**',
            buff=f'**Defense Debuff:** -50% for 2 attacks',
        )


_ABILITIES_INST = Abilities()

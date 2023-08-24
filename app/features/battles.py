from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from dataclasses import dataclass, field
from math import prod
from time import perf_counter_ns
from typing import Any, Generic, Iterator, NamedTuple, TypeAlias, TypeVar, TYPE_CHECKING

import discord

from app.core import Context
from app.core.helpers import MISSING
from app.data.abilities import Ability, AbilityType, Abilities
from app.data.items import Item
from app.database import UserRecord
from app.util.common import image_url_from_emoji, progress_bar
from config import Colors, Emojis

if TYPE_CHECKING:
    from typing import Self

    from app.data.enemies import Enemy
    from app.util.types import TypedInteraction

    Opponent: TypeAlias = discord.Member | Enemy

T = TypeVar('T')


# This includes debuffs
@dataclass
class BuffStackEntry(Generic[T]):
    factor: float
    remaining: int
    types: set[AbilityType] | None = None


class BuffStack(Generic[T]):
    stack: list[BuffStackEntry[T]]

    def __init__(self) -> None:
        self.stack = []

    def __bool__(self) -> bool:
        return bool(self.stack)

    def __len__(self) -> int:
        return len(self.stack)

    def __iter__(self) -> Iterator[BuffStackEntry[T]]:
        return iter(self.stack)

    @property
    def product(self) -> float:
        if not self.stack:
            return 1.0
        return prod(entry.factor for entry in self.stack)

    @property
    def sum(self) -> float:
        if not self.stack:
            return 0.0
        return sum(entry.factor for entry in self.stack)

    def append(self, factor: float, duration: int, *, types: set[AbilityType] | None = None) -> None:
        self.stack.append(BuffStackEntry(factor=factor, remaining=duration, types=types))

    def tick(self, last_used_ability_type: AbilityType) -> None:
        for entry in self.stack:
            if entry.types and last_used_ability_type not in entry.types:
                continue
            entry.remaining -= 1

        self.stack = [entry for entry in self.stack if entry.remaining > 0]


@dataclass
class Player:
    user: Opponent
    hp: int
    stamina: int
    abilities: dict[Ability, int]
    active_message: discord.InteractionMessage | None = None
    attack_stack: BuffStack[float] = field(default_factory=BuffStack)
    defense_stack: BuffStack[float] = field(default_factory=BuffStack)  # Note: this ticks when the OPPONENT moves
    accuracy_stack: BuffStack[float] = field(default_factory=BuffStack)
    max_hp: int = field(init=False)
    max_stamina: int = field(init=False)

    def __post_init__(self) -> None:
        self.max_hp = self.hp
        self.max_stamina = self.stamina

    def __eq__(self, other: Player) -> bool:
        return self.user == other.user

    def __hash__(self) -> int:
        return hash(self.user)

    @property
    def attack_buff(self) -> float:
        return self.attack_stack.product

    @property
    def defense_buff(self) -> float:
        return self.defense_stack.product

    @property
    def accuracy(self) -> float:
        return self.accuracy_stack.product

    def tick_offensive(self, last_used_ability_type: AbilityType) -> None:
        self.attack_stack.tick(last_used_ability_type)
        self.accuracy_stack.tick(last_used_ability_type)

    def tick_defensive(self, last_used_ability_type: AbilityType) -> None:
        self.defense_stack.tick(last_used_ability_type)

    def heal(self, hp: int) -> int:
        hp = min(hp, self.max_hp - self.hp)
        self.hp += hp
        return hp

    @classmethod
    def from_record(cls, member: discord.Member, record: UserRecord) -> Self:
        return cls(
            member,
            hp=record.battle_hp,
            stamina=record.battle_stamina,
            abilities={
                Abilities.punch: 1,
                Abilities.kick: 1,
                Abilities.block: 1,
            },  # TODO this is just a test
        )


class BattleContext(NamedTuple):
    battle: BattleView  # the battle view
    player: Player  # the player who used the ability
    target: Player  # the target opponent
    ability: Ability  # the ability used
    level: int  # the level of the ability

    @property
    def inner(self) -> Context:
        return self.battle.ctx

    def _transform(self, text: str) -> str:
        return f'{self.ability.emoji} {text}'

    def add_attack_commentary(
        self,
        *,
        text: str,
        buff: str | None = None,
        damage: int | None = None,
        critical: bool = False,
    ) -> None:
        self.battle.add_attack_commentary(
            author=self.player,
            victim=self.target,
            text=self._transform(text),
            buff=buff,
            damage=damage,
            critical=critical,
        )

    def add_heal_commentary(self, *, text: str, heal: int) -> None:
        self.battle.add_heal_commentary(author=self.player, text=self._transform(text), heal=heal)

    def add_buff_commentary(self, *, player: Player, text: str, buff: str) -> None:
        self.battle.add_buff_commentary(player=player, text=self._transform(text), buff=buff)

    def deal_attack(self, hp: int, *, tick: bool = True) -> int:
        return self.battle.deal_attack(self.player, self.target, hp, stamina=self.ability.stamina, tick=tick)


class AttackCommentaryEntry(NamedTuple):
    timestamp_ns: int
    author: Player
    victim: Player
    text: str
    buff: str | None = None
    damage: int | None = None
    critical: bool = False


class HealCommentaryEntry(NamedTuple):
    timestamp_ns: int
    author: Player
    text: str
    heal: int


class BuffCommentaryEntry(NamedTuple):
    timestamp_ns: int
    player: Player
    text: str
    buff: str


class SimpleCommentaryEntry(NamedTuple):
    timestamp_ns: int
    text: str


CommentaryEntry: TypeAlias = (
    AttackCommentaryEntry | HealCommentaryEntry | BuffCommentaryEntry | SimpleCommentaryEntry
)


class AbilityButton(discord.ui.Button):
    def __init__(self, parent: BattleView, *, player: Player, target: Player, ability: Ability, level: int) -> None:
        super().__init__(style=discord.ButtonStyle.blurple, label=ability.name, emoji=ability.emoji)
        self.parent = parent
        self.player = player
        self.target = target
        self.ability = ability
        self.level = level

    async def callback(self, interaction: TypedInteraction) -> None:
        if not interaction.user == self.player.user:
            return await interaction.response.send_message('It is not your turn.', ephemeral=True)

        if not await self.parent.ability_check(interaction):
            return

        ctx = BattleContext(
            battle=self.parent, player=self.player, target=self.target, ability=self.ability, level=self.level,
        )
        await self.ability.dispatch(ctx)
        self.parent.check_winner()

        if not self.parent.is_finished():
            await self.parent.advance(self.player)

        if self.parent.is_finished() or self.player.hp <= 0 or self.player.stamina <= 0:
            for button in self.view.children:
                if isinstance(button, AbilityButton):
                    button.disabled = True

        embeds = self.parent.get_player_embeds(self.player)
        await interaction.response.edit_message(content=self.parent.content, embeds=embeds, view=self.view)


class BattleView(discord.ui.View):
    """Base battle view"""

    opponent: Opponent

    NO_WINNER = 0
    TEAM_WON = 1
    ENEMY_WON = 2

    def __init__(
        self,
        ctx: Context,
        records: dict[discord.Member, UserRecord],
        *,
        # None = everyone, MISSING = author only, list = specific team (i.e., union)
        team: list[discord.Member] | None = MISSING,
        opponent: Opponent,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self.ctx = ctx
        self.records: dict[discord.Member, UserRecord] = records
        self.team = [ctx.author] if team is MISSING else team
        self.opponent = opponent
        self.commentary: list[CommentaryEntry] = []
        self.damage_dealt: dict[Player, int] = defaultdict(int)

        self._embed_color = Colors.primary
        self._initially_solo = self.team and len(self.team) == 1

    async def ability_check(self, interaction: TypedInteraction) -> bool:
        return True

    def deal_attack(self, author: Player, target: Player, hp: int, *, stamina: int, tick: bool = True) -> int:
        hp = max(0, round(hp * author.attack_buff * target.defense_buff))
        target.hp = max(0, target.hp - hp)
        author.stamina = max(0, author.stamina - stamina)

        if tick:
            author.tick_offensive(AbilityType.attack)
            target.tick_defensive(AbilityType.attack)

        self.damage_dealt[author] += hp
        return hp

    def add_attack_commentary(
        self,
        *,
        author: Player,
        victim: Player,
        text: str,
        buff: str | None = None,
        damage: int | None = None,
        critical: bool = False,
    ) -> None:
        self.commentary.append(AttackCommentaryEntry(perf_counter_ns(), author, victim, text, buff, damage, critical))

    def add_simple_commentary(self, text: str) -> None:
        self.commentary.append(SimpleCommentaryEntry(perf_counter_ns(), text))

    def add_heal_commentary(self, *, author: Player, text: str, heal: int) -> None:
        self.commentary.append(HealCommentaryEntry(perf_counter_ns(), author, text, heal))

    def add_buff_commentary(self, *, player: Player, text: str, buff: str) -> None:
        self.commentary.append(BuffCommentaryEntry(perf_counter_ns(), player, text, buff))

    @staticmethod
    def format_commentary_entry(entry: CommentaryEntry) -> str:
        if isinstance(entry, AttackCommentaryEntry) and not entry.buff:
            return entry.text

        if isinstance(entry, (HealCommentaryEntry, SimpleCommentaryEntry)):
            return entry.text

        if isinstance(entry, (AttackCommentaryEntry, BuffCommentaryEntry)):
            return f'{entry.text}\n{Emojis.space}{Emojis.Expansion.standalone} {entry.buff}'

    @property
    def formatted_commentary(self) -> str:
        return (
            '\n'.join('- ' + self.format_commentary_entry(entry) for entry in self.commentary[-4:])
            or 'Make a move to start the battle!'
        )

    @property
    def base_embed(self) -> discord.Embed:
        ctx = self.ctx
        return discord.Embed(color=self._embed_color, timestamp=ctx.now)

    @classmethod
    def format_player_buffs(cls, player: Player) -> str:
        buff_text = []
        if player.attack_buff != 1:
            buff_text.append(f'- **ATK** {player.attack_buff - 1:+.1%}')
        if player.defense_buff != 1:
            buff_text.append(f'- **DEF** {-player.defense_buff + 1:+.1%}')
        if player.accuracy != 1:
            buff_text.append(f'- **ACC** {player.accuracy - 1:+.1%}')

        return '\n'.join(buff_text)

    def make_player_embed(self, player: Player) -> discord.Embed:
        embed = self.base_embed
        embed.set_author(name=f'{player.user.name}')
        embed.set_thumbnail(url=player.user.display_avatar)
        embed.add_field(
            name=f'{Emojis.hp} **{player.hp:,}**/{player.max_hp:,} HP',
            value=progress_bar(player.hp / player.max_hp, length=8),
            inline=False,
        )
        embed.add_field(
            name=f'{Emojis.bolt} **{player.stamina:,}**/{player.max_stamina:,} Stamina',
            value=progress_bar(player.stamina / player.max_stamina, length=8),
            inline=False,
        )

        if player.hp <= 0 or player.stamina <= 0:
            embed.colour = Colors.error
        if player.stamina <= 0:
            embed.description = "**You ran out of stamina!** Better rest for a while."

        if buff_text := self.format_player_buffs(player):
            embed.add_field(name='\U0001fa84 Buffs & Debuffs', value=buff_text)

        return embed

    @property
    def content(self) -> str | None:
        return discord.utils.MISSING

    def get_player_embeds(self, player: Player) -> list[discord.Embed]:
        raise NotImplementedError

    def check_winner(self) -> int:
        raise NotImplementedError

    async def advance(self, player: Player) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        super().stop()


class PvEBattleView(BattleView):
    """PvE battle view"""

    opponent: Enemy

    def __init__(
        self,
        ctx: Context,
        records: dict[discord.Member, UserRecord],
        *,
        # None = everyone, MISSING = author only, list = specific team (i.e., union)
        team: list[discord.Member] | None = MISSING,
        opponent: Enemy,
        level: int,
        title: str | None = None,
        description: str | None = None,
        time_limit: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(ctx, records, team=team, opponent=opponent, **kwargs)

        self.players: dict[discord.Member, Player] = {
            member: Player.from_record(member, records[member]) for member in self.team
        } if self.team else {}

        self.opponent_level: int = level
        self.opponent_player: Player = Player(
            opponent,
            hp=opponent.hp_at_level(level),
            stamina=0,  # opponents have infinite stamina
            abilities={ability: level for ability in opponent.abilities},
        )

        self._artifacts: defaultdict[discord.Member, defaultdict[Item, int]] = defaultdict(lambda: defaultdict(int))
        self._embed_author = title
        self._embed_description = description or opponent.description
        if time_limit:
            self.__time_limit_task = ctx.bot.loop.create_task(self._exhaust(time_limit))

        if self._initially_solo:
            self.clear_items()
            player = self.solo_player
            for ability, level in player.abilities.items():
                self.add_item(
                    AbilityButton(self, player=player, target=self.opponent_player, ability=ability, level=level),
                )
            self.add_item(self.surrender)

        if self.team is None:
            self.ctx.bot.loop.create_task(self._update_loop())

        self.won: bool = False
        self._lost: set[discord.Member] = set()

    @classmethod
    def public(
        cls,
        ctx: Context,
        *,
        opponent: Enemy,
        level: int,
        title: str | None = None,
        description: str | None = None,
        time_limit: float | None = None,
        **kwargs: Any,
    ) -> Self:
        return cls(
            ctx,
            records={},
            team=None,
            opponent=opponent,
            level=level,
            title=title,
            description=description,
            time_limit=time_limit,
            **kwargs,
        )

    async def _exhaust(self, time_limit: float) -> None:
        await asyncio.sleep(time_limit)
        if not self.is_finished():
            self.stop()
            self._embed_author = Colors.error
            self.add_simple_commentary(
                f'\u23f1\ufe0f **Time\'s up!** You couldn\'t defeat {self.opponent.display} in time.',
            )
            await self.ctx.maybe_edit(embeds=self.make_public_embeds(), view=self)

    async def _update_loop(self) -> None:
        prev_hp = self.opponent_player.hp
        prev_commentary_len = len(self.commentary)

        while not self.is_finished():
            await asyncio.sleep(1)
            if self.opponent_player.hp == prev_hp and len(self.commentary) == prev_commentary_len:
                continue

            prev_hp = self.opponent_player.hp
            prev_commentary_len = len(self.commentary)
            await self.ctx.maybe_edit(embeds=self.make_public_embeds(), view=self)

    async def interaction_check(self, interaction: TypedInteraction) -> bool:
        if self.team is None:
            if interaction.user not in self.records:
                self.records[interaction.user] = await self.ctx.db.get_user_record(interaction.user.id)

            if interaction.user not in self.players:
                record = self.records[interaction.user]
                self.players[interaction.user] = Player.from_record(interaction.user, record)
            return True

        if interaction.user in self.team:
            return True

        await interaction.response.send_message('no can do buddy', ephemeral=True)
        return False

    async def ability_check(self, interaction: TypedInteraction) -> bool:
        if interaction.user in self._lost:
            await interaction.response.send_message(
                "You've already been defeated and cannot participate in this battle anymore.",
                ephemeral=True,
            )
            return False

        return True

    @property
    def solo(self) -> discord.Member | None:
        if self._initially_solo:
            return self.team[0]
        return None

    @property
    def solo_player(self) -> Player | None:
        return self.players.get(self.solo)

    def make_enemy_embed(self) -> discord.Embed:
        embed = self.base_embed
        embed.title = f'Fighting **{self.opponent.display}**'

        if author := self._embed_author:
            embed.set_author(name=author, icon_url=image_url_from_emoji('\u2694\ufe0f'))

        embed.description = self._embed_description
        embed.set_thumbnail(url=image_url_from_emoji(self.opponent.emoji))

        ratio = self.opponent_player.hp / self.opponent_player.max_hp
        embed.add_field(
            name=f'{Emojis.hp} **{self.opponent_player.hp:,}**/{self.opponent_player.max_hp:,} HP',
            value=progress_bar(ratio, length=8),
            inline=False,
        )
        embed.add_field(name='\U0001f4e3 Commentary', value=self.formatted_commentary, inline=False)
        return embed

    def make_public_embeds(self) -> list[discord.Embed]:
        enemy = self.make_enemy_embed()
        if solo := self.solo_player:
            enemy.timestamp = None
            return [enemy, self.make_player_embed(solo)]

        return [enemy]

    def get_player_embeds(self, player: Player) -> list[discord.Embed]:
        if self.solo:
            return self.make_public_embeds()

        return [self.make_player_embed(player)]

    def check_winner(self) -> int:
        if self.opponent_player.hp <= 0:
            self.stop()
            self.add_simple_commentary(
                f'\N{CROWN} **{self.solo.name}** has defeated **{self.opponent.display}**!'
                if self.solo
                else f'\N{CROWN} **{self.opponent.name}** has been defeated!'
            )
            self._embed_color = Colors.success
            self.won = True
            return self.TEAM_WON

        if self.team is not None and all(player.hp <= 0 or player.stamina <= 0 for player in self.players.values()):
            self.stop()
            self.add_simple_commentary(
                f'**{self.opponent.display}** has defeated **{self.solo.name}**. Better luck next time!'
                if self.solo
                else f'**{self.opponent.display}** has defeated everyone. Better luck next time!'
            )
            self._embed_color = Colors.error
            return self.ENEMY_WON

        for player in self.players.values():
            if (player.hp <= 0 or player.stamina <= 0) and player.user not in self._lost:
                self._lost.add(player.user)
                self.add_simple_commentary(
                    f'\N{SKULL} **{player.user}** has been defeated by **{self.opponent.display}**!',
                )

        return self.NO_WINNER

    async def advance(self, player: Player) -> None:
        """Have the enemy deal a return attack on the player."""
        favored = set()
        # user above 60% HP? favor offensive abilities
        if player.hp / player.max_hp > 0.6:
            favored.add(AbilityType.attack)
            favored.add(AbilityType.buff)
        # below 40% HP? favor healing and defensive abilities
        elif self.opponent_player.hp / self.opponent_player.max_hp < 0.4:
            favored.add(AbilityType.healing)
            favored.add(AbilityType.defense)

        mapping = self.opponent.abilities
        choices, weights = zip(*(
            ((ability, level), (3 if ability.type in favored else 1) * mapping[ability])
            for ability, level in self.opponent_player.abilities.items()
        ))
        ability, level = random.choices(choices, weights=weights)[0]
        await ability.dispatch(BattleContext(self, self.opponent_player, player, ability, level))
        self.check_winner()

    @discord.ui.button(label='Make a Move', style=discord.ButtonStyle.primary)
    async def make_a_move(self, interaction: TypedInteraction, _button: discord.ui.Button) -> Any:
        view = discord.ui.View()
        player = self.players[interaction.user]
        for ability, level in player.abilities.items():
            view.add_item(
                AbilityButton(self, player=player, target=self.opponent_player, ability=ability, level=level)
            )

        await interaction.response.send_message(embed=self.make_player_embed(player), view=view, ephemeral=True)
        player.active_message = await interaction.original_response()

    @discord.ui.button(label='Surrender', style=discord.ButtonStyle.danger)
    async def surrender(self, interaction: TypedInteraction, _button: discord.ui.Button) -> Any:
        if interaction.user in self._lost:
            return await interaction.response.send_message(
                'You\'re already out of the battle, there is no point in surrendering',
                ephemeral=True,
            )

        if self.solo:
            self.stop()
            self._embed_color = Colors.error
            self.add_simple_commentary(f'\U0001f3f3\ufe0f **{self.solo.name} surrendered!** What a coward.')
            return await interaction.response.edit_message(embeds=self.make_public_embeds(), view=self)

        player = self.players[interaction.user]
        self.add_simple_commentary(
            text=f'\U0001f3f3\ufe0f **{player.user} surrenders** and leaves the battle in shame.',
        )
        self._lost.add(player.user)
        self.players.pop(player.user, None)
        self.check_winner()
        await interaction.response.edit_message(embeds=self.make_public_embeds(), view=self)


class PvPBattleView(BattleView):
    opponent: discord.Member

    def __init__(
        self,
        ctx: Context,
        *,
        record: UserRecord,
        challenger: discord.Member,
        challenger_record: UserRecord,
    ) -> None:
        records = {ctx.author: record, challenger: challenger_record}
        super().__init__(ctx, opponent=challenger, records=records)
        self._turn = random.random() < 0.5  # True if player turn, False if opponent turn

        self.winner: discord.Member | None = None
        self.author_player = Player.from_record(ctx.author, record)
        self.opponent_player = Player.from_record(challenger, challenger_record)
        self._update_view()

    async def interaction_check(self, interaction: TypedInteraction, /) -> bool:
        if interaction.user == self.ctx.author or interaction.user == self.opponent:
            return True

        await interaction.response.send_message('go away', ephemeral=True)

    async def ability_check(self, interaction: TypedInteraction) -> bool:
        if interaction.user == self.current_player.user:
            return True

        await interaction.response.send_message('It is not your turn to make a move.', ephemeral=True)
        return False

    @property
    def current_player(self) -> Player:
        return self.author_player if self._turn else self.opponent_player

    def _update_view(self) -> None:
        player = self.current_player
        opponent = self.opponent_player if self._turn else self.author_player
        self.clear_items()
        for ability, level in player.abilities.items():
            self.add_item(
                AbilityButton(self, player=player, target=opponent, ability=ability, level=level),
            )
        self.add_item(self.surrender)

    def check_winner(self) -> int:
        if self.author_player.hp <= 0 or self.author_player.stamina <= 0:
            self.winner = self.opponent
            self._embed_color = Colors.error
            self.stop()
            return self.ENEMY_WON

        if self.opponent_player.hp <= 0 or self.opponent_player.stamina <= 0:
            self.winner = self.ctx.author
            self._embed_color = Colors.success
            self.stop()
            return self.TEAM_WON

        return self.NO_WINNER

    async def advance(self, player: Player) -> None:
        self._turn = not self._turn
        self._update_view()

    @property
    def content(self) -> str:
        if winner := self.winner:
            return f'\N{CROWN} **{winner.mention} has won the battle!**'
        return f'**{self.current_player.user.mention}, make a move!**'

    @classmethod
    def _format_player(cls, player: Player) -> str:
        hp_ratio = player.hp / player.max_hp
        stamina_ratio = player.stamina / player.max_stamina
        base = (
            f'{Emojis.hp} {progress_bar(hp_ratio, length=6)} **{player.hp:,}**/{player.max_hp:,} HP\n'
            f'{Emojis.bolt} {progress_bar(stamina_ratio, length=6)} **{player.stamina:,}**/{player.max_stamina:,} Stamina\n'
        )
        if buff_text := cls.format_player_buffs(player):
            base += '**\U0001fa84 Buffs & Debuffs**\n' + buff_text

        return base

    def get_player_embeds(self, _player: Any) -> list[discord.Embed]:
        embed = self.base_embed
        embed.set_author(name=f'{self.ctx.author} vs. {self.opponent}')
        embed.set_thumbnail(url=self.current_player.user.display_avatar)
        embed.description = f'Waiting for {self.current_player.user.mention} to make a move...'
        embed.add_field(
            name=self.ctx.author.name,
            value=self._format_player(self.author_player),
            inline=False,
        )
        embed.add_field(
            name=self.opponent.name,
            value=self._format_player(self.opponent_player),
            inline=False,
        )
        embed.add_field(name='\U0001f4e3 Commentary', value=self.formatted_commentary, inline=False)
        return [embed]

    @discord.ui.button(label='Surrender', style=discord.ButtonStyle.danger)
    async def surrender(self, interaction: TypedInteraction, _button: discord.ui.Button) -> Any:
        self.stop()
        self._embed_color = Colors.error
        self.winner = self.opponent if interaction.user == self.ctx.author else self.ctx.author
        self.add_simple_commentary(f'\U0001f3f3\ufe0f **{interaction.user} surrendered!** What a coward.')
        return await interaction.response.edit_message(
            content=self.content, embeds=self.get_player_embeds(None), view=self,
        )

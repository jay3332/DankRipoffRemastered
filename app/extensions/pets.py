from __future__ import annotations

import asyncio
import random
import time
from typing import Iterator, TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands
from discord.ext.commands import BadArgument
from discord.utils import format_dt

from app.core import Cog, Context, command, group, simple_cooldown
from app.core.helpers import EDIT, REPLY, cooldown_message, user_max_concurrency
from app.data.items import Item, ItemType, NetMetadata
from app.data.pets import Pet, Pets
from app.util.common import (
    image_url_from_emoji,
    ordinal,
    progress_bar,
    query_collection,
    query_collection_many,
    walk_collection,
)
from app.util.pagination import FieldBasedFormatter, LineBasedFormatter, Paginator
from app.util.views import UserView
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.database import PetRecord, UserRecord
    from app.util.types import CommandResponse, TypedInteraction

    query_pet = Pet
else:
    def query_pet(query: str) -> Pet:
        if pet := query_collection(Pets, Pet, query):
            return pet
        raise BadArgument(f'No pet named {query!r} found.')


class PetsCog(Cog, name='Pets'):
    """Hunt, manage, and raise your pets!"""

    emoji = '\U0001f415\u200d\U0001f9ba'

    def __setup__(self) -> None:
        self._last_measured_api_latency: float | None = None
        self._last_measured_api_latency_minute: int | None = None

    async def get_api_latency(self, default: float = 0.1) -> float:
        # discordstatus refreshes API latency every minute, so we can cache the value for a minute
        perf_minute = time.perf_counter_ns() // 60_000_000_000  # determine the current minute
        if perf_minute == self._last_measured_api_latency_minute and self._last_measured_api_latency is not None:
            # if we've already measured the latency this minute, return the last measured value
            return self._last_measured_api_latency

        api_latency = default  # this is a graceful method, assume a default latency
        timeout = aiohttp.ClientTimeout(total=3)
        try:
            # endpoint to discordstatus which can tell us how the REST API is performing today
            async with self.bot.session.get(
                'https://discordstatus.com/metrics-display/5k2rt9f7pmny/day.json',
                timeout=timeout,
            ) as resp:
                data = await resp.json()
                api_latency = self._last_measured_api_latency = data['metrics'][0]['summary']['mean'] / 1000
                self._last_measured_api_latency_minute = perf_minute
        except asyncio.TimeoutError:
            pass
        return api_latency

    async def get_tolerable_wait_time(self, *, window: float, min: float = 0.5) -> float:
        # within the allowed window of time to react and click:
        # 1. bot sends request to REST
        # 2. user receives request through gateway (assuming they use official discord client)
        # 3. user reacts
        # 4. user clicks button, sending interaction through REST
        # 5. bot receives interaction through gateway
        # in total, a response takes the time of two WS messages plus two REST messages plus reaction time
        api_latency = await self.get_api_latency(default=0.1)  # default to 0.1s if we can't get the latency
        predicted = (self.bot.average_latency + api_latency) * 2  # Predicted response time
        return window + max(
            predicted,
            min,  # If Discord is performing well today, (as if it ever does), a lower bound can be specified
        )

    HUNT_DEFAULT_WEIGHTS = {
        None: 1,
        Pets.dog: 0.8,
        Pets.cat: 0.8,
        Pets.bird: 0.8,
        Pets.bee: 0.01,
    }

    @command(aliases={'catch', 'hu', 'ct'}, hybrid=True)
    @simple_cooldown(1, 90)
    @cooldown_message("There aren't unlimited animals to hunt!")
    @user_max_concurrency(1)
    async def hunt(self, ctx: Context) -> None:
        """Hunt for animals, catch them, and raise them as pets for special powers!"""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()

        available: Iterator[Item[NetMetadata]] = filter(lambda item: item.type is ItemType.net, inventory.cached)
        net: Item[NetMetadata] | None = None
        extra = 'with your bare hands'
        tip = f'\n\U0001f4a1 **Tip:** Buy nets from the shop to hunt rarer pets!'
        try:
            net = max(available, key=lambda item: item.metadata.priority)
            extra = f'with your {net.get_display_name(bold=True)}'
            weights = net.metadata.weights
            tip = ''
        except ValueError:
            weights = self.HUNT_DEFAULT_WEIGHTS

        yield f'{Emojis.loading} Hunting for pets {extra}...{tip}', REPLY

        pet = random.choices(list(weights), weights=list(weights.values()))[0]
        await asyncio.sleep(random.uniform(2, 4))

        if pet is None:
            yield f"You went hunting for pets, but couldn't spot any.", EDIT
            return

        view = HuntView(ctx, record)
        message = (
            f'{ctx.author.mention}, you went hunting for pets and you spotted something...\n'
            'When you see the pet below, click the button to catch it!'
        )
        yield message, view, EDIT
        await asyncio.sleep(random.uniform(3, 6))

        # discord.py has a bit of a problem individually setting a child
        children = view.children  # the children getter implicitly does an implicit copy
        view.clear_items()
        position, bomb_position = random.sample(range(len(children)), k=2)
        if view.is_finished():
            return

        button = None
        for i, child in enumerate(children):
            if i == position:
                view.add_item(button := HuntTargetButton(pet, row=child.row))
                continue
            if i == bomb_position:
                view.add_item(HuntBombButton(net, row=child.row))
                continue
            view.add_item(child)

        sleep_time = await self.get_tolerable_wait_time(window=1.35)
        yield message, view, EDIT
        await asyncio.sleep(sleep_time)

        if view.is_finished():
            return

        view.finish()
        button.style = discord.ButtonStyle.primary
        yield 'You were too slow and the pet escaped! Better luck next time.', view, EDIT

    @group(aliases={'pet', 'zoo', 'p'}, hybrid=True, fallback='view', expand_subcommands=True)
    async def pets(self, ctx: Context) -> CommandResponse:
        """View and manage equipped pets."""
        record = await ctx.db.get_user_record(ctx.author.id)
        pets = await record.pet_manager.wait()

        if not pets.cached:
            mention = ctx.bot.tree.get_app_command(self.hunt.app_command.qualified_name).mention
            return f'You have no pets! Go hunt for some with {mention}.'

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f"{ctx.author.name}'s Equipped Pets", icon_url=ctx.author.avatar)
        embed.set_thumbnail(url=image_url_from_emoji('\U0001f43e'))

        equip_mention = ctx.bot.tree.get_app_command('pets equip').mention
        unequip_mention = ctx.bot.tree.get_app_command('pets unequip').mention
        swap_mention = ctx.bot.tree.get_app_command('pets swap').mention

        if not pets.equipped_count:
            embed.description = (
                f"You haven't equipped any pets yet. Pets must be equipped to use their powers!\n"
                f"Use {equip_mention} to equip pets."
            )
            return embed, REPLY

        embed.description = f'You have equipped **{pets.equipped_count}**/{record.max_equipped_pets} pets.\n'
        embed.description += (
            f'Use {equip_mention} to equip more pets.'
            if pets.equipped_count < record.max_equipped_pets
            else (
                f'You have filled all pet slots! Use {unequip_mention} to unequip pets.\n'
                f'You can also use {swap_mention} to swap pets.'
            )
        )

        fields = [
            {
                'name': f'**{entry.pet.display}** \u2014 {entry.pet.rarity.name.title()}',
                'value': _format_entry(entry),
                'inline': False,
            }
            for entry in pets.cached.values()
            if entry.equipped
        ]
        formatter = FieldBasedFormatter(embed=embed, field_kwargs=fields, per_page=5)
        return Paginator(ctx, formatter), REPLY

    @pets.command(name='all', aliases={'a', '*', 'discovered'}, hybrid=True)
    async def pets_all(self, ctx: Context) -> CommandResponse:
        """List all discovered pets."""
        record = await ctx.db.get_user_record(ctx.author.id)
        pets = await record.pet_manager.wait()

        if not pets.cached:
            mention = ctx.bot.tree.get_app_command(self.hunt.app_command.qualified_name).mention
            return f'You have no pets! Go hunt for some with {mention}.'

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.description = (
            f'You have discovered **{len(pets.cached)}** out of {sum(1 for _ in walk_collection(Pets, Pet))} pets.'
        )
        embed.set_author(name=f"{ctx.author.name}'s Pets", icon_url=ctx.author.avatar)
        embed.set_thumbnail(url=image_url_from_emoji('\U0001f43e'))

        formatter = LineBasedFormatter(
            embed=embed,
            field_name=f'Discovered Pets ({len(pets.cached)})',
            lines=[
                f'**{entry.pet.display}** \u2014 {entry.pet.rarity.name.title()} (Level {entry.level:,}) '
                + ('[EQUIPPED]' if entry.equipped else '')
                for entry in pets.cached.values()
            ],
            per_page=10,
        )

        return Paginator(ctx, formatter), REPLY

    @pets.command(name='info', aliases={'v', 'view', 'i'}, hybrid=True)
    @app_commands.describe(pet='The pet to view information on.')
    async def pets_info(self, ctx: Context, *, pet: query_pet) -> CommandResponse:
        """View information on a particular pet."""
        record = await ctx.db.get_user_record(ctx.author.id)
        pets = await record.pet_manager.wait()
        entry = pets.cached.get(pet)

        evolution = f' (Evolution {entry.evolution})' if entry and entry.evolution else ''
        embed = discord.Embed(
            title=pet.display + evolution, description=pet.description, color=Colors.primary,
            timestamp=ctx.now,
        )
        embed.set_author(name='Pet Info', icon_url=ctx.author.avatar)
        embed.set_thumbnail(url=image_url_from_emoji(pet.emoji))

        embed.add_field(
            name='General',
            value=(
                f'Name: **{pet.display}**\n'
                f'Query Key: `{pet.key}`\n'
                f'Rarity: **{pet.rarity.name.title()}**\n'
                f'Energy Usage: {Emojis.bolt} **{pet.energy_per_minute:,}/minute**'
            ),
        )
        level = 0 if entry is None else entry.level
        embed.add_field(name=f'\U0001f634 Passive Abilities', value=pet.benefit(level), inline=False)
        if abilities := pet.abilities:
            embed.add_field(name=f'{Emojis.bolt} Active Abilities', value=abilities(level), inline=False)

        if entry is None:
            embed.description += '\n*You have not discovered this pet yet.*'
            embed.insert_field_at(
                index=1,
                name='Base Stats',
                value=(
                    f'Max Level: **{pet.max_level:,}**\n'
                    f'Max Energy: {Emojis.bolt} **{pet.max_energy:,}**'
                ),
            )
            return embed, REPLY

        _, exp, requirement = entry.level_data
        ratio = exp / requirement
        embed.add_field(
            name=f'\u2728 **Level {entry.level:,}** ({exp:,}/{requirement} XP)',
            value=(
                f'{progress_bar(ratio, length=8)}  {ratio:.1%}'
                if entry.level < pet.max_level
                else 'MAX LEVEL!'
            ),
            inline=False,
        )
        if entry.equipped:
            ratio = entry.energy / entry.max_energy
            embed.add_field(
                name=f'{Emojis.bolt} **{entry.energy:,}**/{entry.max_energy:,} Energy',
                value=(
                    f'{progress_bar(ratio, length=8)}  {ratio:.1%}\n'
                    + (
                        f'\u23f0 Runs out {format_dt(entry.exhausts_at, "R")}'
                        if entry.energy
                        else (
                            '\n\u26a0\ufe0f **No energy left!** Feed this pet to restore energy.\n'
                            '*Pet abilities only apply when they have enough energy.*'
                        )
                    )
                ),
            )
        else:
            embed.add_field(
                name=f'{Emojis.neutral} Unequipped',
                value=(
                    f'\u26a0\ufe0f Abilities are not active for this pet because it is not equipped.\n'
                    f'To equip this pet, run `{ctx.clean_prefix}pets equip {pet.key}`.'
                )
            )
        embed.insert_field_at(
            index=1,
            name='Stats',
            value=(
                f'Duplicates: **{entry.duplicates:,}**\n'
                f'Max Level: **{pet.max_level:,}**\n'
                f'Max Energy: {Emojis.bolt} **{pet.max_energy:,}**'
            )
        )
        return embed, REPLY

    @pets.command(name='equip', aliases={'+', 'e', 'eq', 'activate', 'add'}, hybrid=True)
    @app_commands.describe(pet='The pet to equip.')
    async def pets_equip(self, ctx: Context, *, pet: query_pet) -> CommandResponse:
        """Equip a pet. This will activate its effects and abilities."""
        record = await ctx.db.get_user_record(ctx.author.id)
        pets = await record.pet_manager.wait()
        if pet not in pets.cached:
            return f'You have not discovered a **{pet.display}** yet.', REPLY

        entry = pets.cached[pet]
        if entry.equipped:
            return f'Your **{pet.display}** is already equipped.', REPLY

        if pets.equipped_count >= record.max_equipped_pets:
            swap_mention = ctx.bot.tree.get_app_command('pets swap').mention
            return (
                f'You have filled all {record.max_equipped_pets} equipped pet slots.\n'
                f'Use {swap_mention} to swap out a pet instead.'
            ), REPLY

        await entry.update(equipped=True)
        ctx.bot.loop.create_task(ctx.thumbs())
        return f'Equipped your **{pet.display}**.', REPLY

    @pets.command(name='unequip', aliases={'-', 'u', 'deactivate', 'remove'}, hybrid=True)
    @app_commands.describe(pet='The pet to unequip.')
    async def pets_unequip(self, ctx: Context, *, pet: query_pet) -> CommandResponse:
        """Unequip a pet. This will remove its effects and abilities."""
        record = await ctx.db.get_user_record(ctx.author.id)
        pets = await record.pet_manager.wait()
        if pet not in pets.cached:
            return f'You have not discovered a **{pet.display}** yet.', REPLY

        entry = pets.cached[pet]
        if not entry.equipped:
            return f'Your **{pet.display}** is not equipped.', REPLY

        await entry.update(equipped=False)
        ctx.bot.loop.create_task(ctx.thumbs())
        return f'Unequipped your **{pet.display}**.', REPLY

    @pets.command(name='swap', aliases={'s', 'switch', 'change', '~'}, hybrid=True)
    @app_commands.describe(to_unequip='The pet to unequip and replace.', to_equip='The pet to equip.')
    async def pets_swap(self, ctx: Context, to_unequip: query_pet, *, to_equip: query_pet) -> CommandResponse:
        """Swap an equipped pet with an unequipped pet."""
        if to_unequip == to_equip:
            return 'You cannot swap a pet with itself.', REPLY

        record = await ctx.db.get_user_record(ctx.author.id)
        pets = await record.pet_manager.wait()
        if to_unequip not in pets.cached:
            return f'You have not discovered a **{to_unequip.display}** yet.', REPLY
        if to_equip not in pets.cached:
            return f'You have not discovered a **{to_equip.display}** yet.', REPLY

        unequip_entry = pets.cached[to_unequip]
        equip_entry = pets.cached[to_equip]
        if not unequip_entry.equipped:
            return f'Your **{to_unequip.display}** is not equipped.', REPLY
        if equip_entry.equipped:
            return f'Your **{to_equip.display}** is already equipped.', REPLY

        async with ctx.db.acquire() as conn:
            await unequip_entry.update(equipped=False, connection=conn)
            await equip_entry.update(equipped=True, connection=conn)

        ctx.bot.loop.create_task(ctx.thumbs())
        return f'Swapped your **{to_unequip.display}** with your **{to_equip.display}**.', REPLY

    @pets_info.autocomplete('pet')
    @pets_equip.autocomplete('pet')
    @pets_unequip.autocomplete('pet')
    @pets_swap.autocomplete('to_unequip')
    @pets_swap.autocomplete('to_equip')
    async def autocomplete_pet(self, _interaction: TypedInteraction, current: str):
        return [
            app_commands.Choice(name=pet.name, value=pet.key)
            for pet in query_collection_many(Pets, Pet, current)
        ]


def _format_level_data(record: PetRecord) -> str:
    level, current, required = record.level_data
    if level >= record.pet.max_level:
        return f'Level {level} (MAX)'
    return f'Level {level} ({current:,}/{required:,} XP)'


class HuntMissButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(emoji=Emojis.space, row=row)

    async def callback(self, itx: TypedInteraction) -> None:
        self.style = discord.ButtonStyle.red
        self.view.finish()

        await itx.response.edit_message(view=self.view)
        await itx.followup.send('You missed the pet and it ran away! Better aim next time.')


class HuntBombButton(discord.ui.Button['HuntView']):
    def __init__(self, net: Item[NetMetadata], *, row: int) -> None:
        super().__init__(emoji='\U0001f4a3', row=row)
        self.net = net

    async def callback(self, itx: TypedInteraction) -> None:
        self.style = discord.ButtonStyle.red
        self.view.finish()

        lost = f'lost your {self.net.get_display_name(bold=True)} and ' if self.net else ''
        async with self.view.ctx.db.acquire() as conn:
            await self.view.record.inventory_manager.add_item(self.net, -1, connection=conn)
            await self.view.record.make_dead(reason='a bomb exploding while hunting', connection=conn)

        await itx.response.edit_message(view=self.view)
        await itx.followup.send(f'{self.emoji} You clicked the bomb and you explode. You {lost}died.')


class HuntTargetButton(discord.ui.Button['HuntView']):
    def __init__(self, pet: Pet, *, row: int) -> None:
        super().__init__(emoji=pet.emoji, row=row)
        self.pet = pet

    async def callback(self, itx: TypedInteraction) -> None:
        self.style = discord.ButtonStyle.green
        self.view.finish()

        embed = discord.Embed(color=Colors.success)
        embed.set_author(name=f'{itx.user.name}: Caught a pet!', icon_url=itx.user.avatar)
        embed.set_thumbnail(url=image_url_from_emoji(str(self.emoji)))
        embed.description = f'You caught a {self.pet.rarity.name} **{self.pet.display}**!\n*{self.pet.description}*'

        pets = await self.view.record.pet_manager.wait()
        if pet_record := pets.cached.get(self.pet):
            previous_level = pet_record.level

            exp = 0 if pet_record.is_max_level else random.randint(5, 15)
            await pet_record.add(exp=exp, duplicates=1)

            embed.description += f'\nThis is your **{ordinal(pet_record.duplicates)}** duplicate.'
            new_level = pet_record.level

            text = (
                '**MAX LEVEL**' if pet_record.is_max_level
                else f'{self.pet.name} XP **+{exp:,}** ({pet_record.exp:,}/{pet_record.exp_requirement:,})'
            )
            embed.add_field(
                name='\U0001f4cb Duplicate Pet!',
                value=f'You already have a **{self.pet.display}**.\n\u2728 {text}',
                inline=False
            )
            if new_level > previous_level:
                embed.add_field(
                    name=f'\U0001f52e **LEVEL UP!** {previous_level} {Emojis.arrow} {new_level}',
                    value=f'**Updated Abilities:**\n{self.pet.full_abilities(new_level)}',
                    inline=False,
                )
        else:
            await pets.add_pet(self.pet)
            embed.add_field(name=f'\U0001f634 Passive Abilities', value=self.pet.benefit(0), inline=False)
            if abilities := self.pet.abilities:
                embed.add_field(name=f'{Emojis.bolt} Active Abilities', value=abilities(0), inline=False)

        embed.set_footer(text=f'Use {self.view.ctx.clean_prefix}pets info {self.pet.key} to see more details!')
        await itx.response.edit_message(view=self.view)
        await itx.followup.send(embed=embed)


class HuntView(UserView):  # CHANGE TO UserView
    def __init__(self, ctx: Context, record: UserRecord) -> None:
        super().__init__(ctx.author, timeout=15)  # if this doesn't time out in 15 seconds, an error likely occured
        for i in range(20):
            self.add_item(HuntMissButton(row=i % 5))
        self.ctx = ctx
        self.record = record

    def finish(self) -> None:
        self.stop()
        for button in self.children:
            button.disabled = True


def _format_entry(entry: PetRecord) -> str:
    expansion = Emojis.Expansion
    exhaustion = (
        f'(exhausts {format_dt(entry.exhausts_at, "R")})'
        if entry.energy > 0 else '\u26a0\ufe0f'
    )
    return (
        f'{expansion.first} \u2728 {_format_level_data(entry)}\n'
        f'{expansion.last} {Emojis.bolt} {entry.energy:,}/{entry.max_energy:,} Energy {exhaustion}'
    )


setup = PetsCog.simple_setup

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.core import BAD_ARGUMENT, Cog, Context, REPLY, command, simple_cooldown, user_max_concurrency
from app.features.battles import PvPBattleView

if TYPE_CHECKING:
    from app.util.types import CommandResponse


class Combat(Cog):
    """Commands related to training and participating in combat and battling."""

    emoji = '\u2694\ufe0f'

    @command('fight', aliases=('battle', 'combat'))
    @simple_cooldown(1, 40)
    @user_max_concurrency(1)
    async def fight(self, ctx: Context, *, user: discord.Member) -> CommandResponse:
        """Challenge someone to a PvP fight."""
        if user.bot:
            return 'You cannot fight bots.', BAD_ARGUMENT
        if user == ctx.author:
            return 'You cannot fight yourself, that\'d be REALLY funny', BAD_ARGUMENT

        if not await ctx.confirm(
            f'{user.mention}, **{ctx.author.name}** challenges you to a fight!\nDo you accept the challenge?',
            user=user,
            timeout=60,
            true='Accept the Challenge',
            false='Decline',
            delete_after=True,
        ):
            return f'{ctx.author.mention}, {user.name} declined your fight.', REPLY

        record = await ctx.db.get_user_record(ctx.author.id)
        challenger_record = await ctx.db.get_user_record(user.id)

        view = PvPBattleView(ctx, record=record, challenger=user, challenger_record=challenger_record)
        return view.content, *view.get_player_embeds(None), view, REPLY


setup = Combat.simple_setup

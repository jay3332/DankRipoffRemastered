from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.core import Cog, Context, REPLY, command, simple_cooldown, user_max_concurrency
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
        record = await ctx.db.get_user_record(ctx.author.id)
        challenger_record = await ctx.db.get_user_record(user.id)

        view = PvPBattleView(ctx, record=record, challenger=user, challenger_record=challenger_record)
        return view.content, *view.get_player_embeds(None), view, REPLY


setup = Combat.simple_setup

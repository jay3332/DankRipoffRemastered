from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

import discord
from discord.utils import format_dt, oauth_url

from app.core import Cog, Context, EDIT, REPLY, command, simple_cooldown
from app.util.structures import Timer

if TYPE_CHECKING:
    pass


class Miscellaneous(Cog):
    """Miscellaneous commands."""

    PONG_MESSAGES = (
        'Pong.',
        'Pong!',
        'Pong?',
        'Pong!?',
    )

    @command(alias="pong")
    @simple_cooldown(2, 2)
    async def ping(self, ctx: Context) -> tuple[str, Any]:
        """Pong! Sends the bot's API latency."""

        word = random.choice(self.PONG_MESSAGES)

        with Timer() as timer:
            if not ctx.is_interaction:
                await ctx.send(word, reference=ctx.message)
            else:
                await ctx.interaction.response.send_message(word)

        time_ms = timer.time * 1000
        return f'{word} ({time_ms:.2f} ms)', EDIT

    @command()
    @simple_cooldown(1, 3)
    async def uptime(self, ctx: Context) -> tuple[str, Any]:
        """Shows the bot's uptime."""

        startup = ctx.bot.startup_timestamp
        return f'I have been online since {format_dt(startup)} ({format_dt(startup, "R")}).', REPLY

    @command(alias='link')
    @simple_cooldown(2, 2)
    async def invite(self, ctx: Context) -> tuple[str, discord.ui.View, Any]:
        """Gives you a link to invite the bot to your server."""
        link = oauth_url(
            ctx.bot.user.id, permissions=discord.Permissions(414531833025), scopes=['bot', 'applications.commands'],
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Click here to invite me to your server!', url=link))

        return f'<{link}>', view, REPLY


setup = Miscellaneous.simple_setup

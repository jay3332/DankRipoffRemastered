from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

from app.core import Cog, Context, EDIT, command, simple_cooldown
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


setup = Miscellaneous.simple_setup

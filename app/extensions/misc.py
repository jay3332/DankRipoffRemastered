from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

from app.core import Cog, Context, EDIT, command, simple_cooldown
from app.util.structures import Timer

if TYPE_CHECKING:
    from app.core import Bot


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
            await ctx.send(word)

        time_ms = timer.time * 1000
        return f'{word} ({time_ms:.2f} ms)', EDIT


def setup(bot: Bot) -> None:
    bot.add_cog(Miscellaneous(bot))

from __future__ import annotations

import os
from typing import Any, ClassVar, Final, TYPE_CHECKING

import discord
import jishaku
from discord.ext import commands

from app.core.models import Context
from app.database import Database
from config import allowed_mentions, beta, beta_token, default_prefix, description, name, owner, token, version

if TYPE_CHECKING:
    from datetime import datetime

jishaku.Flags.HIDE = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.NO_DM_TRACEBACK = True


class Bot(commands.Bot):
    """Dank Ripoff... Remastered."""

    startup_timestamp: datetime

    INTENTS: Final[ClassVar[discord.Intents]] = discord.Intents(
        messages=True,
        members=True,
    )

    def __init__(self) -> None:
        key = 'owner_id' if isinstance(owner, int) else 'owner_ids'

        super().__init__(
            command_prefix=self.__class__.resolve_command_prefix,
            description=description,
            case_insensitive=True,
            allowed_mentions=allowed_mentions,
            intents=self.INTENTS,
            status=discord.Status.dnd,
            max_messages=10,
            **{key: owner},
        )

        self.prepare()

    async def resolve_command_prefix(self, message: discord.Message) -> list[str]:
        """Resolves a command prefix from a message."""
        return commands.when_mentioned_or(default_prefix)(self, message)

    async def _dispatch_first_ready(self) -> None:
        """Waits for the inbound READY gateway event, then dispatches the `first_ready` event."""
        await self.wait_until_ready()
        self.dispatch('first_ready')

    def _load_extensions(self) -> None:
        """Loads all command extensions, including Jishaku."""
        self.load_extension('jishaku')

        for file in os.listdir('./app/extensions'):
            if not file.startswith('_') and file.endswith('.py'):
                self.load_extension(f'app.extensions.{file[:-3]}')

    def prepare(self) -> None:
        """Prepares the bot for startup."""
        self.db: Database = Database(loop=self.loop)
        self.loop.create_task(self._dispatch_first_ready())
        self._load_extensions()

    async def process_commands(self, message: discord.Message, /) -> None:
        if message.author.bot:
            return

        ctx = await self.get_context(message, cls=Context)
        await self.invoke(ctx)

    async def on_first_ready(self) -> None:
        self.startup_timestamp = discord.utils.utcnow()

        text = f'Ready as {self.user} ({self.user.id})'
        center = f' {name} v{version} '

        print(format(center, f'=^{len(text)}'))
        print(text)

    async def on_command_error(self, ctx: Context, error: Exception) -> Any:
        error = getattr(error, 'original', error)

        blacklist = (
            commands.CommandNotFound,
        )
        if isinstance(error, blacklist):
            return

        if isinstance(error, commands.BadArgument):
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(error)

        if isinstance(error, discord.NotFound) and error.code == 10062:
            return

        if isinstance(error, commands.CommandOnCooldown):
            return await ctx.send(f'you have been cooldowned (wait {error.retry_after:.1f} seconds)')

        await ctx.send(f'`panic!({error})`')
        raise error

    def run(self) -> None:
        return super().run(beta_token if beta else token)

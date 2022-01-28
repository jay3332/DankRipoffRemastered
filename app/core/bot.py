from __future__ import annotations

import asyncio
import functools
import os
from textwrap import dedent
from typing import Any, ClassVar, Final, TYPE_CHECKING

import discord
import jishaku
from discord.ext import commands

from app.core.help import HelpCommand
from app.core.models import Context, Command
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
    transaction_locks: dict[int, asyncio.Lock]

    INTENTS: Final[ClassVar[discord.Intents]] = discord.Intents(
        messages=True,
        members=True,
        guilds=True,
    )

    def __init__(self) -> None:
        key = 'owner_id' if isinstance(owner, int) else 'owner_ids'

        super().__init__(
            command_prefix=self.__class__.resolve_command_prefix,
            help_command=HelpCommand(),
            update_application_commands_at_startup=True,
            description=description,
            case_insensitive=True,
            allowed_mentions=allowed_mentions,
            intents=self.INTENTS,
            status=discord.Status.dnd,
            max_messages=10,
            **{key: owner},
        )

        self._BotBase__cogs = commands.core._CaseInsensitiveDict()
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
        self.transaction_locks: dict[int, asyncio.Lock] = {}

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

        if isinstance(error, commands.BadUnionArgument):
            error = error.errors[0]

        blacklist = (
            commands.CommandNotFound,
        )
        if isinstance(error, blacklist):
            return

        if isinstance(error, commands.BadArgument):
            ctx.command.reset_cooldown(ctx)
            return await ctx.send(error)

        if ctx.is_interaction:
            if ctx.interaction.response.is_done():
                respond = ctx.interaction.followup.send
            else:
                respond = functools.partial(ctx.interaction.response.send_message, ephemeral=True)
        else:
            respond = functools.partial(ctx.send, reference=ctx.message)

        if isinstance(error, (commands.ConversionError, commands.MissingRequiredArgument)):
            if error.param is None:
                return await respond("Could not parse your command input properly.")

            ctx.command.reset_cooldown(ctx)
            ansi, length, carets = Command.ansi_signature_until(ctx.command, error.param.name)

            invoked_with = ' '.join((*ctx.invoked_parents, ctx.invoked_with))

            alias_message = (
                f'Hint: \u001b[00;0mcommand alias \u001b[36;1m{invoked_with!r} \u001b[00;0mpoints to '
                f'\u001b[32;1m{ctx.command.qualified_name}\u001b[00;0m, is this corrrect?'
            ) if ctx.command.qualified_name != invoked_with else ''

            # inspired by Rust error messages
            #
            # this looks really nice on PC, but it looks horrible on mobile
            # maybe make it look different on mobile?
            return await respond(dedent(f"""
                Could not parse your command input properly:
                ```ansi
                Attempted to parse signature:
                
                    \u001b[37;1m{ctx.clean_prefix}\u001b[32;1m{invoked_with} {ansi}\u001b[30;1m
                    {' ' * (length + len(ctx.clean_prefix) + len(invoked_with))} {'^' * carets} Error occured here
                
                \u001b[31;1m{error} \u001b[37;1m
                
                {alias_message}
                ```
            """))

        if isinstance(error, discord.NotFound) and error.code == 10062:
            return

        if isinstance(error, commands.CommandOnCooldown):
            return await respond(f'you have been cooldowned (wait {error.retry_after:.1f} seconds)')

        await respond(f'`panic!({error})`')
        raise error

    def run(self) -> None:
        return super().run(beta_token if beta else token)

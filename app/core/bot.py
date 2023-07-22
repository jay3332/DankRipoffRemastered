from __future__ import annotations

import functools
import os
import re
from typing import Any, ClassVar, Final, TYPE_CHECKING

import discord
import jishaku
from aiohttp import ClientSession
from discord.ext import commands

from app.core.help import HelpCommand
from app.core.flags import FlagMeta
from app.core.models import Command, Context
from app.database import Database
from app.util.ansi import AnsiStringBuilder, AnsiColor
from app.util.common import humanize_duration, pluralize
from app.util.structures import LockWithReason
from config import Colors, allowed_mentions, default_prefix, description, name, owner, token, version

if TYPE_CHECKING:
    from datetime import datetime

jishaku.Flags.HIDE = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.NO_DM_TRACEBACK = True

ANSI_REGEX: re.Pattern[str] = re.compile(r"\x1b\[\d{2};[01]m")


class Bot(commands.Bot):
    """Dank Ripoff... Remastered."""

    bypass_checks: bool
    db: Database
    session: ClientSession
    startup_timestamp: datetime
    transaction_locks: dict[int, LockWithReason]

    INTENTS: Final[ClassVar[discord.Intents]] = discord.Intents(
        messages=True,
        message_content=True,
        presences=True,
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

    async def resolve_command_prefix(self, message: discord.Message) -> list[str]:
        """Resolves a command prefix from a message."""
        return commands.when_mentioned_or(default_prefix)(self, message)

    async def _dispatch_first_ready(self) -> None:
        """Waits for the inbound READY gateway event, then dispatches the `first_ready` event."""
        await self.wait_until_ready()
        self.dispatch('first_ready')

    async def _load_extensions(self) -> None:
        """Loads all command extensions, including Jishaku."""
        await self.load_extension('jishaku')

        for file in os.listdir('./app/extensions'):
            if not file.startswith('_') and file.endswith('.py'):
                await self.load_extension(f'app.extensions.{file[:-3]}')

    async def setup_hook(self) -> None:
        """Prepares the bot for startup."""
        self.db = Database(self, loop=self.loop)
        self.transaction_locks = {}
        self.session = ClientSession()
        self.bypass_checks = False

        self.loop.create_task(self._dispatch_first_ready())
        await self._load_extensions()

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

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if message.content in {f'<@{self.user.id}>', f'<@!{self.user.id}>'}:
            await message.reply(
                f"Hey, I'm {self.user.name}. My prefix here is `{default_prefix}`.\nAdditionally, "
                f"some of my commands are available as slash commands.\n\nFor more help, run `{default_prefix}help`."
            )

        await self.process_commands(message)

    async def on_command_error(self, ctx: Context, error: Exception) -> Any:
        # sourcery no-metrics
        error = getattr(error, 'original', error)

        if isinstance(error, commands.BadUnionArgument):
            error = error.errors[0]

        blacklist = (
            commands.CommandNotFound,
            commands.CheckFailure,
        )
        if isinstance(error, blacklist):
            return

        respond = functools.partial(ctx.send, reference=ctx.message, delete_after=30, ephemeral=True)

        if isinstance(error, commands.BadArgument):
            ctx.command.reset_cooldown(ctx)
            return await respond(error)

        if isinstance(error, commands.MaxConcurrencyReached):
            # noinspection PyUnresolvedReferences
            return await respond(
                pluralize(f'Calm down there! This command can only be used {error.number} time(s) at once per {error.per.name}.'),
            )

        if isinstance(error, discord.NotFound) and error.code == 10062:
            return

        if isinstance(error, commands.CommandOnCooldown):
            command = ctx.command

            embed = discord.Embed(color=Colors.error, timestamp=ctx.now)
            embed.set_author(name='Command on cooldown!', icon_url=ctx.author.avatar.url)
            embed.description = getattr(command.callback, '__cooldown_message__', 'Please wait before using this command again.')

            default = pluralize(f'{error.cooldown.rate} time(s) per {humanize_duration(error.cooldown.per)}')

            embed.add_field(name='Try again after', value=humanize_duration(error.retry_after))
            embed.add_field(name='Default cooldown', value=default)

            return await respond(embed=embed)

        if isinstance(error, (commands.ConversionError, commands.MissingRequiredArgument, commands.BadLiteralArgument)):
            ctx.command.reset_cooldown(ctx)
            param = ctx.current_parameter
        elif isinstance(error, commands.MissingRequiredArgument):
            param = error.param
        else:
            await ctx.send(f'panic!({error})', reference=ctx.message)
            raise error

        builder = AnsiStringBuilder()
        builder.append('Attempted to parse command signature:').newline(2)
        builder.append('    ' + ctx.clean_prefix, color=AnsiColor.white, bold=True)

        if ctx.invoked_parents and ctx.invoked_subcommand:
            invoked_with = ' '.join((*ctx.invoked_parents, ctx.invoked_with))
        elif ctx.invoked_parents:
            invoked_with = ' '.join(ctx.invoked_parents)
        else:
            invoked_with = ctx.invoked_with

        builder.append(invoked_with + ' ', color=AnsiColor.green, bold=True)

        command = ctx.command
        signature = Command.ansi_signature_of(command)
        builder.extend(signature)
        signature = signature.raw

        if match := re.search(
                fr"[<\[](--)?{re.escape(param.name)}((=.*)?| [<\[]\w+(\.{{3}})?[>\]])(\.{{3}})?[>\]](\.{{3}})?",
                signature,
        ):
            lower, upper = match.span()
        elif isinstance(param.annotation, FlagMeta):
            param_store = command.params
            old = command.params.copy()

            flag_key, _ = next(filter(lambda p: p[1].annotation is command.custom_flags, param_store.items()))

            del param_store[flag_key]
            lower = len(command.raw_signature) + 1

            command.params = old
            del param_store

            upper = len(command.signature) - 1
        else:
            lower, upper = 0, len(command.signature) - 1

        builder.newline()

        offset = len(ctx.clean_prefix) + len(invoked_with)  # noqa
        content = f'{" " * (lower + offset + 5)}{"^" * (upper - lower)} Error occured here'
        builder.append(content, color=AnsiColor.gray, bold=True).newline(2)
        builder.append(str(error), color=AnsiColor.red, bold=True)

        if invoked_with != ctx.command.qualified_name:
            builder.newline(2)
            builder.append('Hint: ', color=AnsiColor.white, bold=True)

            builder.append('command alias ')
            builder.append(repr(invoked_with), color=AnsiColor.cyan, bold=True)
            builder.append(' points to ')
            builder.append(ctx.command.qualified_name, color=AnsiColor.green, bold=True)
            builder.append(', is this correct?')

        ansi = builder.ensure_codeblock().dynamic(ctx)
        await ctx.send(f'Could not parse your command input properly:\n{ansi}', reference=ctx.message, ephemeral=True)

    async def close(self) -> None:
        await self.session.close()
        await super().close()

    def run(self, token_override: str | None = None, **kwargs) -> None:
        return super().run(token_override or token, **kwargs)

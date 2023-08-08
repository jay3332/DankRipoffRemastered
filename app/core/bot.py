from __future__ import annotations

from asyncio import subprocess
import functools
import math
import os
import re
import sys
from collections import deque
from datetime import datetime, time
from io import BytesIO
from typing import Any, ClassVar, Final, TYPE_CHECKING

import discord
import discord.gateway as gateway
import jishaku
from aiohttp import ClientSession
from discord import app_commands
from discord.ext import commands, tasks

from app.core.help import HelpCommand
from app.core.flags import FlagMeta
from app.core.helpers import ActiveTransactionLock
from app.core.models import Command, Context, GroupCommand
from app.core.timers import TimerManager
from app.database import Database
from app.util.ansi import AnsiStringBuilder, AnsiColor
from app.util.common import humanize_duration, pluralize
from app.util.structures import LockWithReason
from app.util.views import StaticCommandButton
from config import (
    Colors, DatabaseConfig, allowed_mentions, backups_channel, default_prefix,
    description, name, owner, token, version,
)

if TYPE_CHECKING:
    from typing import TypeAlias

    from discord.abc import Snowflake

    AppCommandStore: TypeAlias = dict[str, app_commands.AppCommand]

jishaku.Flags.HIDE = True
jishaku.Flags.NO_UNDERSCORE = True
jishaku.Flags.NO_DM_TRACEBACK = True

ANSI_REGEX: re.Pattern[str] = re.compile(r"\x1b\[\d{2};[01]m")


class TrackingKeepAliveHandler(gateway.KeepAliveHandler):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.latencies = deque[float]()  # track past 5 latencies

    def ack(self) -> None:
        super().ack()
        if math.isinf(self.latency):  # if the latency is infinite, don't add it to the list
            return

        self.latencies.append(self.latency)
        if len(self.latencies) > 5:
            self.latencies.popleft()

    @property
    def average_latency(self) -> float:
        """Returns the average latency of the past 5 heartbeats.

        This is not a simple mean, rather the worst and best latencies out of past 5 are removed,
        then the mean is taken from the remaining 3.
        """
        # No latencies? Return inf to indicate that we don't have any data
        if not self.latencies:
            return float('inf')

        # If there are three latencies or fewer, removing the worst and best latencies do more harm to the accuracy
        # than good, so just return the mean of all the latencies
        if len(self.latencies) <= 3:
            return sum(self.latencies) / len(self.latencies)

        # Remove the worst and best latencies. Since the worst case of sorted is O(n^2) and we're removing 2
        # predictable elements, we can do this in O(n) time instead:
        worst = float('-inf')
        best = float('inf')
        wpos = bpos = 0
        for i, latency in enumerate(self.latencies):
            if latency > worst:
                worst = latency
                wpos = i
            if latency < best:
                best = latency
                bpos = i

        # Remove the worst and best latencies
        values = [latency for i, latency in enumerate(self.latencies) if i not in (wpos, bpos)]
        return sum(values) / len(values)


# we do a lil bit of monkey patching
gateway.KeepAliveHandler = TrackingKeepAliveHandler


# taken from <https://gist.github.com/Soheab/fed903c25b1aae1f11a8ca8c33243131#file-command_tree-py>
# noinspection PyShadowingNames
class Tree(app_commands.CommandTree):
    def __init__(self, bot: Bot, **kwargs):
        super().__init__(bot, **kwargs)
        self._global_app_commands: AppCommandStore = {}
        # guild_id: AppCommandStore
        self._guild_app_commands: dict[int, AppCommandStore] = {}

    def find_app_command_by_names(
        self,
        *qualified_name: str,
        guild: Snowflake | int | None = None,
    ) -> app_commands.AppCommand | None:
        commands = self._global_app_commands
        if guild:
            guild_id = guild.id if not isinstance(guild, int) else guild
            guild_commands = self._guild_app_commands.get(guild_id, {})
            if not guild_commands and self.fallback_to_global:
                commands = self._global_app_commands
            else:
                commands = guild_commands

        for cmd_name, cmd in commands.items():
            if any(name in qualified_name for name in cmd_name.split()):
                return cmd

        return None

    def get_app_command(
        self,
        value: str | int,
        guild: Snowflake | int | None = None,
    ) -> app_commands.AppCommand | None:
        def search_dict(d: AppCommandStore) -> app_commands.AppCommand | None:
            for cmd_name, cmd in d.items():
                if value == cmd_name or (str(value).isdigit() and int(value) == cmd.id):
                    return cmd
            return None

        if guild:
            guild_id = guild.id if not isinstance(guild, int) else guild
            guild_commands = self._guild_app_commands.get(guild_id, {})
            if not self.fallback_to_global:
                return search_dict(guild_commands)
            else:
                return search_dict(guild_commands) or search_dict(self._global_app_commands)
        else:
            return search_dict(self._global_app_commands)

    @staticmethod
    def _unpack_app_commands(commands: list[app_commands.AppCommand]) -> AppCommandStore:
        ret: AppCommandStore = {}

        def unpack_options(
            options: list[app_commands.AppCommand | app_commands.AppCommandGroup | app_commands.Argument],
        ):
            for option in options:
                if isinstance(option, app_commands.AppCommandGroup):
                    ret[option.qualified_name] = option  # type: ignore
                    unpack_options(option.options)  # type: ignore

        for command in commands:
            ret[command.name] = command
            unpack_options(command.options)  # type: ignore

        return ret

    async def _update_cache(
        self,
        commands: list[app_commands.AppCommand],
        guild: Snowflake | int | None = None,
    ) -> None:
        # because we support both int and Snowflake
        # we need to convert it to a Snowflake like object if it's an int
        _guild: Snowflake | None = None
        if guild is not None:
            if isinstance(guild, int):
                _guild = discord.Object(guild)
            else:
                _guild = guild

        if _guild:
            self._guild_app_commands[_guild.id] = self._unpack_app_commands(commands)
        else:
            self._global_app_commands = self._unpack_app_commands(commands)

    async def fetch_command(self, command_id: int, /, *, guild: Snowflake | None = None) -> app_commands.AppCommand:
        res = await super().fetch_command(command_id, guild=guild)
        await self._update_cache([res], guild=guild)
        return res

    async def fetch_commands(self, *, guild: Snowflake | None = None) -> list[app_commands.AppCommand]:
        res = await super().fetch_commands(guild=guild)
        await self._update_cache(res, guild=guild)
        return res

    def clear_app_commands_cache(self, *, guild: Snowflake | None) -> None:
        if guild:
            self._guild_app_commands.pop(guild.id, None)
        else:
            self._global_app_commands = {}

    def clear_commands(
        self,
        *,
        guild: Snowflake | None,
        type: discord.AppCommandType | None = None,
        clear_app_commands_cache: bool = True,
    ) -> None:
        super().clear_commands(guild=guild)
        if clear_app_commands_cache:
            self.clear_app_commands_cache(guild=guild)

    async def sync(self, *, guild: Snowflake | None = None) -> list[app_commands.AppCommand]:
        res = await super().sync(guild=guild)
        await self._update_cache(res, guild=guild)
        return res


class Bot(commands.Bot):
    """Dank Ripoff... Remastered."""

    bypass_checks: bool
    db: Database
    session: ClientSession
    startup_timestamp: datetime
    timers: TimerManager
    tree: Tree
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
            command_prefix=self.__class__.resolve_command_prefix,  # type: ignore
            help_command=HelpCommand(),
            update_application_commands_at_startup=True,
            description=description,
            case_insensitive=True,
            allowed_mentions=allowed_mentions,
            intents=self.INTENTS,
            status=discord.Status.dnd,
            max_messages=10,
            tree_cls=Tree,
            **{key: owner},
        )

        self._BotBase__cogs = commands.core._CaseInsensitiveDict()

    @property
    def average_latency(self) -> float:
        """Returns the average latency of the past 5 heartbeats.

        This is not a simple mean, rather the worst and best latencies out of past 5 are removed,
        then the mean is taken from the remaining 3.
        """
        if not self.ws:
            return float('nan')

        if keep_alive := self.ws._keep_alive:  # type: ignore
            keep_alive: TrackingKeepAliveHandler
            return keep_alive.average_latency

        return float('nan')

    def add_command(self, command: Command, /) -> None:
        if isinstance(command, Command):
            command.transform_flag_parameters()

        if isinstance(command, GroupCommand):
            for child in command.walk_commands():
                if isinstance(child, Command):
                    child.transform_flag_parameters()  # type: ignore

        super().add_command(command)

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
        self.timers = TimerManager(self)
        self.transaction_locks = {}
        self.session = ClientSession()
        self.bypass_checks = False

        self.loop.create_task(self._dispatch_first_ready())
        await self._load_extensions()
        await self.tree.fetch_commands()  # populate cache

    async def get_context(
        self,
        origin: discord.Message | discord.Interaction,
        *,
        cls: type[Context] = Context,
    ) -> Context:
        return await super().get_context(origin, cls=cls)

    async def process_commands(self, message: discord.Message, /) -> None:
        if message.author.bot:
            return

        ctx = await self.get_context(message)
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
                f"Hey, I'm {self.user.name}. My prefix here is **`{default_prefix}`**\nAdditionally, "
                f"most of my commands are available as slash commands.\n\nFor more help, run `{default_prefix}help`."
            )

        await self.process_commands(message)

    @discord.utils.cached_property
    def _cooldowns_remind_command(self) -> Any:
        return self.get_command('cooldowns remind')

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
            view = None
            if isinstance(error, ActiveTransactionLock) and error.lock.jump_url is not None:
                view = discord.ui.View().add_item(
                    discord.ui.Button(label='Jump to Transaction', url=error.lock.jump_url),
                )

            ctx.command.reset_cooldown(ctx)
            return await respond(error, view=view)

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

            view = None
            if error.retry_after > 30:
                view = discord.ui.View(timeout=60).add_item(
                    StaticCommandButton(
                        command=self._cooldowns_remind_command,
                        command_kwargs={'command': command},
                        label='Remind me when I can use this command again',
                        emoji='\u23f0',
                        style=discord.ButtonStyle.primary,
                    )
                )

            return await respond(embed=embed, view=view)

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

    # Run backup every 2 hours
    @tasks.loop(time=[time(hour=hour) for hour in range(0, 24, 2)])
    async def backup(self) -> None:
        await self.wait_until_ready()
        channel = self.get_partial_messageable(backups_channel)
        command = [
            'pg_dump',
            '-d', DatabaseConfig.name,
            *(('-U', DatabaseConfig.user) if DatabaseConfig.user else ()),
            *(('-h', DatabaseConfig.host) if DatabaseConfig.host else ()),
            *(('-p', DatabaseConfig.port) if DatabaseConfig.port else ()),
            '-w'
        ]

        proc = await subprocess.create_subprocess_shell(
            ' '.join(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            await channel.send(
                f'Backup {discord.utils.format_dt(discord.utils.utcnow())} on {sys.platform}',
                file=discord.File(BytesIO(stdout), filename='backup.sql'),
            )
        if stderr:
            await channel.send(
                f'ERROR backing up {discord.utils.format_dt(discord.utils.utcnow())} on {sys.platform} <@{owner}>',
                file=discord.File(BytesIO(stderr), filename='error.txt'),
            )

    async def close(self) -> None:
        await self.session.close()
        await super().close()

    def run(self, token_override: str | None = None, **kwargs) -> None:
        return super().run(token_override or token, **kwargs)

from __future__ import annotations

import inspect
import random
from datetime import timedelta
from functools import wraps
from typing import Any, Callable, Final, Iterable, Literal, TYPE_CHECKING, overload

import discord
from discord.app_commands import Command as AppCommand
from discord.ext import commands

from app.core.models import Command, GroupCommand, HybridCommand, HybridGroupCommand
from app.util.common import format_line, sentinel
from app.util.pagination import Paginator
from app.util.structures import LockWithReason

if TYPE_CHECKING:
    from app.core.models import Context, Cog
    from app.util.types import TypedInteraction

__all__ = (
    'REPLY',
    'EDIT',
    'BAD_ARGUMENT',
    'MISSING',
    'easy_command_callback',
    'command',
    'group',
    'simple_cooldown',
    'database_cooldown',
)

EDIT  = sentinel('EDIT', repr='EDIT')
REPLY = sentinel('REPLY', repr='REPLY')
BAD_ARGUMENT = sentinel('BAD_ARGUMENT', repr='BAD_ARGUMENT')
ERROR = sentinel('ERROR', repr='ERROR')
EPHEMERAL = sentinel('ERROR', repr='EPHEMERAL')
NO_EXTRA = sentinel('NO_EXTRA', repr='NO_EXTRA')

MISSING = sentinel('MISSING', bool=False, repr='MISSING')

CURRENCY_COGS: Final[frozenset[str]] = frozenset({
    'Casino',
    'Combat',
    'Farming',
    'Jobs',
    'Pets',
    'Profit',
    'Skill',
    'Stats',
    'Transactions',
})


def clean_interaction_kwargs(kwargs: dict[str, Any]) -> None:
    # no files in interactions
    kwargs.pop('file', None)
    kwargs.pop('files', None)


async def _into_interaction_response(interaction: TypedInteraction, kwargs: dict[str, Any]) -> None:
    clean_interaction_kwargs(kwargs)
    kwargs.pop('reference', None)

    if kwargs.get('embed') and kwargs.get('embeds') is not None:
        kwargs['embeds'].append(kwargs['embed'])
        del kwargs['embed']

    if kwargs.pop('edit', False):
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)
        except discord.NotFound:
            pass
        else:
            return

    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


class GenericError(commands.BadArgument):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs.get('content', 'Unknown error'))
        self.kwargs = kwargs


with open('assets/tips.txt', 'r') as f:
    TIPS = f.readlines()


async def process_message(ctx: Context, payload: Any) -> discord.Message | None:
    # sourcery no-metrics
    if payload is None:
        return

    kwargs = {}
    # noinspection PyTypeChecker
    kwargs.setdefault('embeds', [])
    # noinspection PyTypeChecker
    kwargs.setdefault('files', [])

    if not isinstance(payload, (set, tuple, list)):
        payload = [payload]

    paginator = None
    extra = True
    edit = False
    error = False

    for part in payload:
        if part is REPLY:
            kwargs['reference'] = ctx.message

        elif part is EDIT:
            kwargs['edit'] = edit = True

        elif part is BAD_ARGUMENT:
            raise commands.BadArgument(kwargs['content'])

        elif part is EPHEMERAL:
            kwargs['ephemeral'] = True

        elif part is ERROR:
            error = True

        elif part is NO_EXTRA:
            extra = False

        elif isinstance(part, discord.Embed):
            kwargs['embeds'].append(part)

        elif isinstance(part, discord.File):
            kwargs['files'].append(part)

        elif isinstance(part, discord.ui.View):
            kwargs['view'] = part

        elif isinstance(part, Paginator):
            paginator = part

        elif isinstance(part, dict):
            kwargs.update(part)

        elif part is None:
            continue

        else:
            kwargs['content'] = str(part)

    if error:
        raise GenericError(**kwargs)

    if extra and ctx.cog.qualified_name in CURRENCY_COGS and not kwargs.get('content') and not edit:
        record = await ctx.db.get_user_record(ctx.author.id)

        if notifs := record.unread_notifications:
            kwargs['content'] = (
                f"\U0001f514 You have {notifs:,} unread notification{'s' if notifs != 1 else ''}. "
                f"Run `{ctx.clean_prefix}notifications` to view them."
            )

        if random.random() < 0.2:
            tip = random.choice(TIPS)
            kwargs.setdefault('content', '')
            kwargs['content'] += f'\n\U0001f4a1 **Tip:** {format_line(ctx, tip)}'

    interaction = getattr(ctx, 'interaction', None)

    if paginator:
        clean_interaction_kwargs(kwargs)
        return await paginator.start(interaction=interaction, **kwargs)

    if interaction:
        return await _into_interaction_response(interaction, kwargs)

    return await ctx.send(**kwargs)


# We are using non-generic "callable" as return types would be
# a bit too complicated
def easy_command_callback(func: callable) -> callable:
    @wraps(func)
    async def wrapper(cog: Cog, ctx: Context, /, *args, **kwargs) -> None:
        coro = func(cog, ctx, *args, **kwargs)

        if inspect.isasyncgen(coro):
            async for payload in coro:
                await process_message(ctx, payload)
        else:
            await process_message(ctx, await coro)

    return wrapper


def _get_lock(ctx: Context, *, update_jump_url: bool = False) -> LockWithReason:
    lock = ctx.bot.transaction_locks.setdefault(ctx.author.id, LockWithReason())
    if update_jump_url:
        lock.jump_url = ctx.message.jump_url
    return lock


class ActiveTransactionLock(commands.BadArgument):
    def __init__(self, lock: LockWithReason) -> None:
        super().__init__(lock.reason or 'Please finish your pending transaction(s) first.')
        self.lock = lock


def lock_transactions(func: callable) -> callable:
    async def check(ctx: Context) -> bool:
        lock = _get_lock(ctx)

        if lock.locked():
            raise ActiveTransactionLock(lock)

        return True

    # yikes

    if inspect.isasyncgenfunction(func):
        @wraps(func)
        async def wrapper(cog: Cog, ctx: Context, /, *args, **kwargs) -> Any:
            async with _get_lock(ctx, update_jump_url=True):
                async for item in func(cog, ctx, *args, **kwargs):
                    yield item

    else:
        @wraps(func)
        async def wrapper(cog: Cog, ctx: Context, /, *args, **kwargs) -> Any:
            async with _get_lock(ctx, update_jump_url=True):
                return await func(cog, ctx, *args, **kwargs)

    return commands.check(check)(wrapper)


# noinspection PyShadowingBuiltins
def _resolve_command_kwargs(
    cls: type,
    *,
    name: str = MISSING,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING
) -> dict[str, Any]:
    kwargs = {'cls': cls}

    if name is not MISSING:
        kwargs['name'] = name

    if alias is not MISSING and aliases is not MISSING:
        raise TypeError('cannot have alias and aliases kwarg filled')

    if alias is not MISSING:
        kwargs['aliases'] = (alias,)

    if aliases is not MISSING:
        kwargs['aliases'] = tuple(aliases)

    if usage is not MISSING:
        kwargs['usage'] = usage

    if brief is not MISSING:
        kwargs['brief'] = brief

    if help is not MISSING:
        kwargs['help'] = help

    return kwargs


# noinspection PyShadowingBuiltins
@overload
def command(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: Literal[True] | AppCommand = False,
    **other_kwargs: Any,
) -> Callable[..., HybridCommand]:
    ...


# noinspection PyShadowingBuiltins
@overload
def command(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: Literal[False] = False,
    **other_kwargs: Any,
) -> Callable[..., Command]:
    ...


# noinspection PyShadowingBuiltins
def command(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: bool | AppCommand = False,
    **other_kwargs: Any,
) -> Callable[..., Command]:
    kwargs = _resolve_command_kwargs(
        HybridCommand if hybrid else Command,
        name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )
    result = commands.command(**kwargs, **other_kwargs)
    if isinstance(hybrid, AppCommand):
        result.app_command = hybrid

    if easy_callback:
        return lambda func: result(easy_command_callback(func))

    return result


# noinspection PyShadowingBuiltins
@overload
def group(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: Literal[True] = False,
    **other_kwargs: Any,
) -> Callable[..., HybridGroupCommand]:
    ...


# noinspection PyShadowingBuiltins
@overload
def group(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: Literal[False] = False,
    **other_kwargs: Any,
) -> Callable[..., GroupCommand]:
    ...


# noinspection PyShadowingBuiltins
def group(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: bool = False,
    iwc: bool = True,
    **other_kwargs: Any,
) -> Callable[..., GroupCommand]:
    kwargs = _resolve_command_kwargs(
        HybridGroupCommand if hybrid else GroupCommand,
        name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )
    kwargs['invoke_without_command'] = iwc
    result = commands.group(**kwargs, **other_kwargs)

    if easy_callback:
        return lambda func: result(easy_command_callback(func))

    return result


def simple_cooldown(rate: int, per: float, bucket: commands.BucketType = commands.BucketType.user) -> Callable[[callable], callable]:
    return commands.cooldown(rate, per, bucket)


def user_max_concurrency(count: int, *, wait: bool = False) -> Callable[[callable], callable]:
    return commands.max_concurrency(count, commands.BucketType.user, wait=wait)


def cooldown_message(message: str) -> Callable[[callable | commands.Command], callable]:
    def decorator(func: callable | commands.Command) -> callable:
        if isinstance(func, commands.Command):
            func = func.callback

        func.__cooldown_message__ = message
        return func

    return decorator


def database_cooldown(per: float, /) -> Callable[[callable], callable]:
    async def predicate(ctx: Context) -> bool:
        data = await ctx.db.get_user_record(ctx.author.id)
        manager = data.cooldown_manager

        await manager.wait()
        cooldown = manager.get_cooldown(ctx.command)

        if cooldown is False:
            expires = discord.utils.utcnow() + timedelta(seconds=per)
            await manager.set_cooldown(ctx.command, expires=expires)

            return True

        raise commands.CommandOnCooldown(commands.Cooldown(1, per), cooldown, commands.BucketType.user)

    deco = commands.check(predicate)

    @wraps(deco)
    def wrapper(func: callable) -> callable:
        func.__database_cooldown__ = per
        return deco(func)

    return wrapper

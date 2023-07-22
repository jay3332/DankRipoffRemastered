from __future__ import annotations

import inspect
from datetime import timedelta
from functools import wraps
from typing import Any, Callable, Final, Iterable, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core.models import Command, GroupCommand, HybridCommand, HybridGroupCommand
from app.util.common import sentinel
from app.util.pagination import Paginator
from app.util.structures import LockWithReason

if TYPE_CHECKING:
    from app.core.models import Context, Cog

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
NO_EXTRA = sentinel('NO_EXTRA', repr='NO_EXTRA')

MISSING = sentinel('MISSING', bool=False, repr='MISSING')

CURRENCY_COGS: Final[frozenset[str]] = frozenset({
    'Casino',
    'Profit',
    'Stats',
    'Transactions',
})


def clean_interaction_kwargs(kwargs: dict[str, Any]) -> None:
    kwargs.pop('reference', None)

    # no files in interactions
    kwargs.pop('file', None)
    kwargs.pop('files', None)


async def _into_interaction_response(interaction: discord.Interaction, kwargs: dict[str, Any]) -> None:
    clean_interaction_kwargs(kwargs)

    if kwargs.get('embed') and kwargs.get('embeds') is not None:
        kwargs['embeds'].append(kwargs['embed'])
        del kwargs['embed']

    if kwargs.pop('edit', False):
        if interaction.response.is_done():
            await interaction.edit_original_message(**kwargs)
        else:
            await interaction.response.edit_message(**kwargs)

        return

    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


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

    for part in payload:
        if part is REPLY:
            kwargs['reference'] = ctx.message

        elif part is EDIT:
            kwargs['edit'] = edit = True

        elif part is BAD_ARGUMENT:
            raise commands.BadArgument(kwargs['content'])

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

    if extra and ctx.cog.qualified_name in CURRENCY_COGS and not kwargs.get('content') and not edit:
        record = await ctx.db.get_user_record(ctx.author.id)

        if notifs := record.unread_notifications:
            kwargs['content'] = (
                f"\U0001f514 You have {notifs:,} unread notification{'s' if notifs != 1 else ''}. "
                f"Run `{ctx.clean_prefix}notifications` to view them."
            )

        # TODO: tips

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


def _get_lock(ctx: Context) -> LockWithReason:
    return ctx.bot.transaction_locks.setdefault(ctx.author.id, LockWithReason())


def lock_transactions(func: callable) -> callable:
    async def check(ctx: Context) -> bool:
        lock = _get_lock(ctx)

        if lock.locked():
            raise commands.BadArgument(lock.reason or 'Please finish your pending transaction(s) first.')

        return True

    # yikes

    if inspect.isasyncgenfunction(func):
        @wraps(func)
        async def wrapper(cog: Cog, ctx: Context, /, *args, **kwargs) -> Any:
            async with _get_lock(ctx):
                async for item in func(cog, ctx, *args, **kwargs):
                    yield item

    else:
        @wraps(func)
        async def wrapper(cog: Cog, ctx: Context, /, *args, **kwargs) -> Any:
            async with _get_lock(ctx):
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
def command(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: bool = False,
    **other_kwargs: Any,
) -> Callable[..., Command]:
    kwargs = _resolve_command_kwargs(
        HybridCommand if hybrid else Command,
        name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )
    result = commands.command(**kwargs, **other_kwargs)

    if easy_callback:
        return lambda func: result(easy_command_callback(func))

    return result


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

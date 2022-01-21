from __future__ import annotations

import inspect
# from datetime import timedelta
from functools import wraps
from typing import Any, Callable, Dict, Iterable, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core.models import Command, GroupCommand
from app.util.common import setinel
from app.util.pagination import Paginator

if TYPE_CHECKING:
    from app.core.models import Context, Cog

__all__ = (
    'REPLY',
    'EDIT',
    'MISSING',
    'easy_command_callback',
    'command',
    'group',
    'simple_cooldown',
    # 'database_cooldown',
)

EDIT  = setinel('EDIT', repr='EDIT')
REPLY = setinel('REPLY', repr='REPLY')

MISSING = setinel('MISSING', bool=False, repr='MISSING')


async def _send_message(ctx: Context, payload: Any) -> discord.Message | None:
    kwargs = {}
    # noinspection PyTypeChecker
    kwargs.setdefault('embeds', [])
    # noinspection PyTypeChecker
    kwargs.setdefault('files', [])

    if not isinstance(payload, (set, tuple, list)):
        payload = [payload]

    paginator = None

    for part in payload:
        if part is REPLY:
            kwargs['reference'] = ctx.message

        elif part is EDIT:
            kwargs['edit'] = True

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
            return

        else:
            kwargs['content'] = str(part)

    if paginator:
        return await paginator.start(**kwargs)

    return await ctx.send(**kwargs)


# We are using non-generic "callable" as return types would be
# a bit too complicated
def easy_command_callback(func: callable) -> callable:
    @wraps(func)
    async def wrapper(cog: Cog, ctx: Context, /, *args, **kwargs) -> None:
        coro = func(cog, ctx, *args, **kwargs)

        if inspect.isasyncgen(coro):
            async for payload in coro:
                await _send_message(ctx, payload)
        else:
            await _send_message(ctx, await coro)

    return wrapper


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
) -> Dict[str, Any]:
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
    **_other_kwargs
) -> Callable[[Any, ...], Command]:
    kwargs = _resolve_command_kwargs(
        Command, name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )
    result = commands.command(**kwargs, **_other_kwargs)

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
    iwc: bool = True,
    **_other_kwargs
) -> Callable[[Any, ...], Command]:
    kwargs = _resolve_command_kwargs(
        GroupCommand, name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )
    kwargs['invoke_without_command'] = iwc
    result = commands.group(**kwargs, **_other_kwargs)

    if easy_callback:
        return lambda func: result(easy_command_callback(func))

    return result


def simple_cooldown(rate: int, per: float, bucket: commands.BucketType = commands.BucketType.user) -> Callable[[callable], callable]:
    return commands.cooldown(rate, per, bucket)


# def database_cooldown(per: float, /) -> Callable[[callable], callable]:
#     async def predicate(ctx: Context) -> bool:
#         data = await ctx.db.get_user_record(ctx.author.id)
#         manager = data.cooldown_manager
#
#         await manager.wait()
#         cooldown = manager.get_cooldown(ctx.command)
#
#         if cooldown is False:
#             expires = discord.utils.utcnow() + timedelta(seconds=per)
#             await manager.set_cooldown(ctx.command, expires=expires)
#             return True
#
#         raise commands.CommandOnCooldown(commands.Cooldown(1, per), cooldown, commands.BucketType.user)
#
#     return commands.check(predicate)

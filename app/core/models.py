from __future__ import annotations

import discord
import datetime

from discord.ext import commands
from discord.utils import maybe_coroutine as maybe_coro

from app.util.types import TypedContext
from app.util.views import AnyUser, ConfirmationView

from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.bot import Bot
    from app.database import Database


class Context(TypedContext):
    def __init__(self, **attrs) -> None:
        self._message: discord.Message | None = None
        super().__init__(**attrs)

    @property
    def db(self) -> Database:
        return self.bot.db

    @property
    def now(self) -> datetime.datetime:
        return self.message.created_at

    async def thumbs(self, message: discord.Message = None) -> None:
        message = message or self.message
        try:
            await message.add_reaction('\U0001f44d')
        except discord.HTTPException:
            pass

    async def confirm(
        self,
        content: str = None,
        *,
        delete_after: bool = False,
        view: ConfirmationView = None,
        user: AnyUser = None,
        timeout: float = 60.,
        true: str = 'Yes',
        false: str = 'No',
        **kwargs,
    ) -> bool:
        user = user or self.author
        view = view or ConfirmationView(user=user, true=true, false=false, timeout=timeout)
        message = await self.send(content, view=view, **kwargs)

        await view.wait()
        if delete_after:
            await message.delete(delay=0)
        else:
            await message.edit(view=view)

        return view.value

    async def send(self, content: Any = None, **kwargs) -> discord.Message:
        if kwargs.get('embed') and kwargs.get('embeds') is not None:
            kwargs['embeds'].append(kwargs['embed'])
            del kwargs['embed']

        if kwargs.get('file') and kwargs.get('files') is not None:
            kwargs['files'].append(kwargs['file'])
            del kwargs['file']

        if kwargs.pop('edit', False) and self._message:
            kwargs.pop('files', None)
            kwargs.pop('reference', None)

            await self._message.edit(content=content, **kwargs)
            return self._message

        self._message = result = await super().send(content, **kwargs)
        return result


class Cog(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        bot.loop.create_task(maybe_coro(self.__setup__))

    def __setup__(self) -> Awaitable[None] | None:
        pass


class Command(commands.Command):
    ...


class GroupCommand(commands.Group, Command):
    # 25% boilerplate code but discord.py does it like this, so...
    def command(self, *args, **kwargs) -> Callable[..., Command]:
        # noinspection PyArgumentList
        def decorator(func):
            from app.core.helpers import command

            kwargs.setdefault('parent', self)
            result = command(*args, **kwargs)(func)
            self.add_command(result)
            return result

        return decorator

    def group(self, *args, **kwargs) -> Callable[..., GroupCommand]:
        # noinspection PyArgumentList
        def decorator(func):
            from app.core.helpers import group as _group

            kwargs.setdefault('parent', self)
            result = _group(*args, **kwargs)(func)
            self.add_command(result)
            return result

        # noinspection PyTypeChecker
        return decorator

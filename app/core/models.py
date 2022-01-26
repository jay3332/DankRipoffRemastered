from __future__ import annotations

import discord
import datetime

from discord.ext import commands
from discord.utils import maybe_coroutine as maybe_coro

from app.util.types import TypedContext
from app.util.views import AnyUser, ConfirmationView

from typing import Any, Awaitable, Callable, Literal, TYPE_CHECKING, Union

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

    @property
    def clean_prefix(self) -> str:
        if self.prefix is None:
            return ''

        user = self.bot.user
        return self.prefix.replace(f'<@{user.id}>', f'@{user.name}').replace(f'<@!{user.id}>', f'@{user.name}')

    @property
    def is_interaction(self) -> bool:
        return bool(getattr(self, 'interaction', False))

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

    async def maybe_edit(self, message: discord.Message, content: Any = None, **kwargs: Any) -> discord.Message | None:
        try:
            await message.edit(content=content, **kwargs)
        except (AttributeError, discord.NotFound):
            if (not message) or message.channel == self.channel:
                return await self.send(content, **kwargs)

            return await message.channel.send(content, **kwargs)

    @staticmethod
    async def maybe_delete(message: discord.Message, *args: Any, **kwargs: Any) -> None:
        try:
            await message.delete(*args, **kwargs)
        except (AttributeError, discord.NotFound, discord.Forbidden):
            pass

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

            await self.maybe_edit(self._message, content, **kwargs)
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
    def ansi_signature(self) -> str:
        return self.ansi_signature_until("$")[0]

    # This code consists of purely hell.
    # The base of this method was taken from the actual library, but I cba to actually optimize it nor it's changes.
    def ansi_signature_until(self, until: str) -> tuple[str, int, int]:  # sourcery no-metrics
        params = self.clean_params
        if not params:
            return '', 0, 0

        result = []
        count = length = 0
        got = False

        for name, param in params.items():
            greedy = isinstance(param.annotation, commands.Greedy)
            optional = False  # postpone evaluation of if it's an optional argument

            # for typing.Literal[...], typing.Optional[typing.Literal[...]], and Greedy[typing.Literal[...]], the
            # parameter signature is a literal list of it's values
            annotation = param.annotation.converter if greedy else param.annotation
            origin = getattr(annotation, '__origin__', None)
            if not greedy and origin is Union:
                none_cls = type(None)
                union_args = annotation.__args__
                optional = union_args[-1] is none_cls
                if len(union_args) == 2 and optional:
                    annotation = union_args[0]
                    origin = getattr(annotation, '__origin__', None)

            if origin is Literal:
                name = '|'.join(f'"{v}"' if isinstance(v, str) else str(v) for v in annotation.__args__)
            if param.default is not param.empty:
                # We don't want None or '' to trigger the [name=value] case and instead it should
                # do [name] since [name=None] or [name=] are not exactly useful for the user.
                should_print = param.default if isinstance(param.default, str) else param.default is not None

                if should_print:
                    result.append(r := (
                        f'\u001b[34;1m[{name}={param.default}]'
                        if not greedy else f'\u001b[34;1m[{name}={param.default}]...'
                    ))

                    if name == until or got:
                        if not length:
                            length = len(r) - 7
                        got = True
                    else:
                        count += len(r) - 6

                    continue
                else:
                    result.append(f'\u001b[34;1m[{name}]')
                    if name != until and not got:
                        count += len(name) + 3
                    else:
                        if not length:
                            length = len(name) + 2
                        got = True

            elif param.kind == param.VAR_POSITIONAL:
                if self.require_var_positional:
                    result.append(f'\u001b[33;1m<{name}...>')
                else:
                    result.append(f'\u001b[34;1m[{name}...]')

                if name == until or got:
                    if not length:
                        length = len(name) + 5
                    got = True
                else:
                    count += len(name) + 6
            elif greedy:
                result.append(f'\u001b[34;1m[{name}]...')

                if name != until and not got:
                    count += len(name) + 6
                else:
                    if not length:
                        length = len(name) + 5
                    got = True
            elif optional:
                result.append(f'\u001b[34;1m[{name}]')
                if name == until or got:
                    if not length:
                        length = len(name) + 2
                    got = True
                else:
                    count += len(name) + 3
            else:
                result.append(f'\u001b[33;1m<{name}>')
                if name != until and not got:
                    count += len(name) + 3
                else:
                    if not length:
                        length = len(name) + 2
                    got = True

        return ' '.join(result), count, length


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

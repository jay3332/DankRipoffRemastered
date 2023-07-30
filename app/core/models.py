from __future__ import annotations

import datetime
import functools
from collections import OrderedDict
from functools import wraps
from typing import Any, Awaitable, Callable, NamedTuple, Literal, TYPE_CHECKING, Union, Self

import discord
from discord.app_commands import Command as AppCommand, Group as AppGroup
from discord.ext import commands
from discord.utils import MISSING, maybe_coroutine as maybe_coro

from app.core.flags import ConsumeUntilFlag, FlagMeta, Flags
from app.util.ansi import AnsiColor, AnsiStringBuilder
from app.util.structures import TemporaryAttribute
from app.util.types import TypedContext
from app.util.views import AnyUser, ConfirmationView

if TYPE_CHECKING:
    from typing import ClassVar

    from app.core.bot import Bot
    from app.database import Database
    from app.util.types import AsyncCallable, TypedInteraction


class Context(TypedContext):
    bot: Bot
    command: Command | GroupCommand

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
        return self.interaction is not None

    @staticmethod
    def utcnow() -> datetime.datetime:
        return discord.utils.utcnow()

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
        interaction: TypedInteraction = None,
        user: AnyUser = None,
        timeout: float = 60.,
        true: str = 'Yes',
        false: str = 'No',
        **kwargs,
    ) -> bool:
        user = user or self.author
        view = view or ConfirmationView(user=user, true=true, false=false, timeout=timeout)
        message = None
        if interaction is not None:
            await interaction.response.send_message(content, view=view, **kwargs)
        else:
            message = await self.send(content, view=view, **kwargs)

        await view.wait()
        if interaction is None:
            if delete_after:
                await message.delete(delay=0)
            else:
                await message.edit(view=view)
        else:
            await interaction.edit_original_response(view=view)

        return view.value

    async def maybe_edit(self, message: discord.Message, content: Any = None, **kwargs: Any) -> discord.Message | None:
        try:
            await message.edit(content=content, **kwargs)
        except (AttributeError, discord.NotFound):
            if not message or message.channel == self.channel:
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
    __hidden__: bool = False
    emoji: ClassVar[str]

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        bot.loop.create_task(maybe_coro(self.__setup__))  # type: ignore

    def __setup__(self) -> Awaitable[None] | None:
        pass

    @classmethod
    async def simple_setup(cls, bot: Bot) -> None:
        await bot.add_cog(cls(bot))


class PermissionSpec(NamedTuple):
    """A pair of sets of permissions which will be checked for before a command is run."""
    user: set[str]
    bot: set[str]

    @classmethod
    def new(cls) -> Self:
        """Creates a new permission spec.

        Users default to requiring no permissions.
        Bots default to requiring Read Message History, View Channel, Send Messages, Embed Links, and External Emojis permissions.
        """
        required = {'read_message_history', 'view_channel', 'send_messages', 'embed_links', 'use_external_emojis'}
        return cls(user=set(), bot=required)

    @staticmethod
    def permission_as_str(permission: str) -> str:
        """Takes the attribute name of a permission and turns it into a capitalized, readable one."""
        # weird and hacky solution but it works.
        return permission.replace('_', ' ').title().replace('Tts ', 'TTS ').replace('Guild', 'Server')

    @staticmethod
    def _is_owner(bot: Bot, user: discord.User) -> bool:
        if bot.owner_id:
            return user.id == bot.owner_id

        elif bot.owner_ids:
            return user.id in bot.owner_ids

        return False

    # We don't use @has_permissions/@bot_has_permissions as I may want to implement custom permission checks later on
    def check(self, ctx: Context) -> bool:
        """Checks if the given context meets the required permissions."""
        if ctx.bot.bypass_checks and self._is_owner(ctx.bot, ctx.author):
            return True

        other = ctx.channel.permissions_for(ctx.author)
        missing = [perm for perm, value in other if perm in self.user and not value]

        if missing and not other.administrator:
            raise commands.MissingPermissions(missing)

        other = ctx.channel.permissions_for(ctx.me)
        missing = [perm for perm, value in other if perm in self.bot and not value]

        if missing and not other.administrator:
            raise commands.BotMissingPermissions(missing)

        return True


class ParamInfo(NamedTuple):
    """Parameter information."""
    name: str
    required: bool
    default: Any
    greedy: bool
    choices: list[str | int | bool] | None
    show_default: bool
    flag: bool
    store_true: bool

    def is_flag(self) -> bool:
        return self.flag


@discord.utils.copy_doc(commands.Command)
class Command(commands.Command):
    def __init__(self, func: AsyncCallable[..., Any], **kwargs: Any) -> None:
        self._permissions: PermissionSpec = PermissionSpec.new()
        if user_permissions := kwargs.pop('user_permissions', None):
            self._permissions.user.update(user_permissions)

        if bot_permissions := kwargs.pop('bot_permissions', None):
            self._permissions.bot.update(bot_permissions)

        self.custom_flags: FlagMeta[Any] | None = None

        super().__init__(func, **kwargs)
        self.add_check(self._permissions.check)

    def _ensure_assignment_on_copy(self, other: Command) -> Command:
        super()._ensure_assignment_on_copy(other)

        other._permissions = self._permissions
        other.custom_flags = self.custom_flags

        return other

    def transform_flag_parameters(self) -> None:
        first_consume_rest = None

        for name, param in self.params.items():
            if param.kind is not param.KEYWORD_ONLY:
                continue

            try:
                is_flags = issubclass(param.annotation, Flags)
            except TypeError:
                is_flags = False

            if is_flags:
                self.custom_flags = param.annotation
                try:
                    default = self.custom_flags.default
                except ValueError:
                    pass
                else:
                    self.params[name] = param.replace(default=default)

                if not first_consume_rest:
                    break

                target = self.params[first_consume_rest]
                default = MISSING if target.default is param.empty else target.default
                annotation = None if target.annotation is param.empty else target.annotation

                self.params[first_consume_rest] = target.replace(
                    annotation=ConsumeUntilFlag(annotation, default),
                    kind=param.POSITIONAL_OR_KEYWORD,
                )
                break

            elif not first_consume_rest:
                first_consume_rest = name

        if first_consume_rest and self.custom_flags:  # A kw-only has been transformed into a pos-or-kw, reverse this here
            @wraps(original := self.callback)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                idx = 2 if self.cog else 1

                for i, (arg, (k, v)) in enumerate(zip(args[idx:], self.params.items())):
                    if k == first_consume_rest:
                        args = args[:i + idx]
                        kwargs[k] = arg
                        break

                return await original(*args, **kwargs)

            self._callback = wrapper  # leave the params alone

    @classmethod
    def ansi_signature_of(cls, command: commands.Command, /) -> AnsiStringBuilder:
        if isinstance(command, cls):
            return command.ansi_signature  # type: ignore

        with TemporaryAttribute(command, attr='custom_flags', value=None):
            return cls.ansi_signature.fget(command)

    @property
    def permission_spec(self) -> PermissionSpec:
        """Return the permission specification for this command.

        Useful for the help command.
        """
        return self._permissions

    @staticmethod
    def _disect_param(param: commands.Parameter) -> tuple:
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

        return annotation, greedy, optional, origin

    @property
    def param_info(self) -> OrderedDict[str, ParamInfo]:
        """Returns a dict mapping parameter names to their rich info."""
        result = OrderedDict()
        params = self.clean_params
        if not params:
            return result

        for name, param in params.items():
            annotation, greedy, optional, origin = Command._disect_param(param)
            default = param.default

            if isinstance(annotation, FlagMeta) and self.custom_flags:
                for flag in self.custom_flags.walk_flags():
                    optional = not flag.required
                    name = '--' + flag.name
                    default = param.empty

                    if not flag.store_true and flag.default or flag.default is False:
                        default = flag.default
                        optional = True

                    result[name] = ParamInfo(
                        name=name,
                        required=not optional,
                        show_default=bool(flag.default) or flag.default is False,
                        default=default,
                        choices=None,
                        greedy=greedy,
                        flag=True,
                        store_true=flag.store_true,
                    )

                continue

            choices = annotation.__args__ if origin is Literal else None

            if default is not param.empty:
                show_default = bool(default) if isinstance(default, str) else default is not None
                optional = True
            else:
                show_default = False

            if param.kind is param.VAR_POSITIONAL:
                optional = not self.require_var_positional
            elif param.default is param.empty:
                optional = optional or greedy

            result[name] = ParamInfo(
                name=name,
                required=not optional,
                show_default=show_default,
                default=default,
                choices=choices,
                greedy=greedy,
                flag=False,
                store_true=False,
            )

        return result

    @property
    def ansi_signature(self) -> AnsiStringBuilder:  # sourcery no-metrics
        """Returns an ANSI builder for the signature of this command."""
        if self.usage is not None:
            return AnsiStringBuilder(self.usage)

        params = self.clean_params
        result = AnsiStringBuilder()
        if not params:
            return result

        for name, param in params.items():
            annotation, greedy, optional, origin = Command._disect_param(param)

            if isinstance(annotation, FlagMeta) and self.custom_flags:
                for flag in self.custom_flags.walk_flags():
                    start, end = '<>' if flag.required else '[]'
                    base = '--' + flag.name

                    result.append(start, bold=True, color=AnsiColor.gray)
                    result.append(base, color=AnsiColor.yellow if flag.required else AnsiColor.blue)

                    if not flag.store_true:
                        result.append(' <', color=AnsiColor.gray, bold=True)
                        result.append(flag.dest, color=AnsiColor.magenta)

                        if flag.default or flag.default is False:
                            result.append('=', color=AnsiColor.gray)
                            result.append(str(flag.default), color=AnsiColor.cyan)

                        result.append('>', color=AnsiColor.gray, bold=True)

                    result.append(end + ' ', color=AnsiColor.gray, bold=True)

                continue

            if origin is Literal:
                name = '|'.join(f'"{v}"' if isinstance(v, str) else str(v) for v in annotation.__args__)

            if param.default is not param.empty:
                # We don't want None or '' to trigger the [name=value] case, and instead it should
                # do [name] since [name=None] or [name=] are not exactly useful for the user.
                should_print = param.default if isinstance(param.default, str) else param.default is not None
                result.append('[', color=AnsiColor.gray, bold=True)
                result.append(name, color=AnsiColor.blue)

                if should_print:
                    result.append('=', color=AnsiColor.gray, bold=True)
                    result.append(str(param.default), color=AnsiColor.cyan)
                    extra = '...' if greedy else ''
                else:
                    extra = ''

                result.append(']' + extra + ' ', color=AnsiColor.gray, bold=True)
                continue

            elif param.kind == param.VAR_POSITIONAL:
                if self.require_var_positional:
                    start = '<'
                    end = '...>'
                else:
                    start = '['
                    end = '...]'

            elif greedy:
                start = '['
                end = ']...'

            elif optional:
                start, end = '[]'
            else:
                start, end = '<>'

            result.append(start, color=AnsiColor.gray, bold=True)
            result.append(name, color=AnsiColor.blue if start == '[' else AnsiColor.yellow)
            result.append(end + ' ', color=AnsiColor.gray, bold=True)

        return result

    @property
    def signature(self) -> str:
        """Adds POSIX-like flag support to the signature"""
        return self.ansi_signature.raw

    @property
    def raw_signature(self) -> str:
        return super().signature


@discord.utils.copy_doc(commands.HybridCommand)
class HybridCommand(Command, commands.HybridCommand):
    def define_app_command(self, **kwargs: Any) -> Callable[[AsyncCallable[..., Any]], AppCommand]:
        def decorator(func: AsyncCallable[..., Any]) -> AppCommand:
            @functools.wraps(func)
            async def wrapper(slf: Cog, itx: TypedInteraction, *args: Any, **kwds: Any) -> Any:
                # TODO: call full hooks?
                # FIXME: this is a bit hacky

                # this is especially hacky
                self.cog = slf
                ctx = await slf.bot.get_context(itx)
                ctx.command = self

                if not await self.can_run(ctx):
                    return
                return await func(slf, ctx, *args, **kwds)

            command = discord.app_commands.Command(
                name=self.name,
                description=self.short_doc,
                parent=self.parent.app_command if isinstance(self.parent, HybridGroupCommand) else None,
                callback=wrapper,
                **kwargs,
            )
            if isinstance(self.parent, HybridGroupCommand) and self.parent.app_command is not None:
                self.parent.app_command.add_command(command)
            return command

        return decorator


@discord.utils.copy_doc(commands.Group)
class GroupCommand(commands.Group, Command):
    @discord.utils.copy_doc(commands.Group.command)
    def command(self, *args: Any, **kwargs: Any) -> Callable[[AsyncCallable[..., Any]], Command | HybridCommand]:
        def decorator(func: AsyncCallable[..., Any]) -> Command:
            from app.core.helpers import command

            kwargs.setdefault('parent', self)
            result = command(*args, **kwargs)(func)
            self.add_command(result)
            return result

        return decorator

    @discord.utils.copy_doc(commands.Group.group)
    def group(self, *args: Any, **kwargs: Any) -> Callable[[AsyncCallable[..., Any]], GroupCommand | HybridGroupCommand]:
        def decorator(func: AsyncCallable[..., Any]) -> GroupCommand:
            from app.core.helpers import group

            kwargs.setdefault('parent', self)
            result = group(*args, **kwargs)(func)
            self.add_command(result)
            return result

        return decorator


@discord.utils.copy_doc(commands.HybridGroup)
class HybridGroupCommand(GroupCommand, commands.HybridGroup):
    pass

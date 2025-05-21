from __future__ import annotations

from typing import Any, AsyncGenerator, AsyncIterable, Awaitable, Callable, ParamSpec, TYPE_CHECKING, TypeAlias, TypeVar

from discord import Embed, File, Interaction
from discord.ext import commands
from discord.ui import LayoutView, View

from app.util.ansi import AnsiStringBuilder
from app.util.common import ConstantT
from app.util.pagination import Paginator

if TYPE_CHECKING:
    from inspect import Parameter

    from discord import ClientUser, Guild, Member, Message, User, VoiceProtocol
    from discord.abc import Messageable
    from discord.interactions import InteractionChannel, InteractionResponse

    from app.core.bot import Bot
    from app.core.models import Command, Cog

P = ParamSpec('P')
R = TypeVar('R')

__all__ = (
    'TypedContext',
    'TypedInteraction',
)

AsyncCallable: TypeAlias = Callable[P, Awaitable[R] | AsyncIterable[R]]

CommandResponseFragment: TypeAlias = (
    str | Embed | File | Paginator | View | LayoutView | dict[str, Any] | ConstantT | AnsiStringBuilder
)
SingleCommandResponse: TypeAlias = CommandResponseFragment | tuple[CommandResponseFragment, ...]
CommandResponse: TypeAlias = SingleCommandResponse | AsyncGenerator[SingleCommandResponse, Any]
OptionalCommandResponse: TypeAlias = CommandResponse | None


class TypedInteraction(Interaction):
    client: Bot
    channel: InteractionChannel
    response: InteractionResponse[Bot]


# noinspection PyPropertyDefinition
class _TypedContext:
    message: Message
    bot: Bot
    args: list[Any]
    kwargs: dict[str, Any]
    current_parameter: Parameter | None
    prefix: str
    command: Command
    invoked_with: str
    invoked_parents: list[str]
    invoked_subcommand: Command | None
    subcommand_passed: str
    command_failed: bool

    # May not be present
    interaction: TypedInteraction | None

    @property
    def valid(self) -> bool:
        ...

    @property
    def clean_prefix(self) -> str:
        ...

    @property
    def cog(self) -> Cog:
        ...

    @property
    def guild(self) -> Guild:
        ...

    @property
    def channel(self) -> Messageable:
        ...

    @property
    def me(self) -> Member | ClientUser:
        ...

    @property
    def author(self) -> Member | User:
        ...

    @property
    def voice_client(self) -> VoiceProtocol | None:
        ...


if TYPE_CHECKING:
    class TypedContext(_TypedContext):
        ...
else:
    class TypedContext(commands.Context):
        interaction: TypedInteraction | None

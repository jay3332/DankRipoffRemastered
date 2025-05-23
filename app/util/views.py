from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, TypeAlias, TYPE_CHECKING

import discord

from app.util.structures import TemporaryAttribute

if TYPE_CHECKING:
    from app.core.models import Command, Context, GroupCommand, HybridCommand, HybridGroupCommand
    from app.util.types import TypedInteraction

AnyUser: TypeAlias = discord.User | discord.Member


class AnyUserView(Protocol):
    user: AnyUser
    _user_view_owner_bypass: bool


class OwnerBypassButton(discord.ui.Button[AnyUserView]):
    def __init__(self, parent: AnyUserView) -> None:
        self.parent: AnyUserView = parent
        super().__init__(style=discord.ButtonStyle.primary, label='Enable Owner Bypass')

    async def callback(self, interaction: TypedInteraction) -> None:
        self.parent._user_view_owner_bypass = True
        await interaction.response.edit_message(content='Enabled owner bypass. You may now use the view.', view=None)


async def _user_view_interaction_check(view: AnyUserView, interaction: TypedInteraction) -> bool:
    if interaction.user != view.user:
        if await interaction.client.is_owner(interaction.user):
            if view._user_view_owner_bypass:
                return True

            view = discord.ui.View(timeout=30).add_item(OwnerBypassButton(view))
            await interaction.response.send_message(
                content='Enable owner bypass to use this component view.',
                ephemeral=True,
                view=view,
            )
            return False

        message = f'This component view is owned by {view.user.mention}, therefore you cannot use it.'
        await interaction.response.send_message(content=message, ephemeral=True)
        return False

    return True


class UserView(discord.ui.View):
    def __init__(self, user: AnyUser, *, timeout: float = None) -> None:
        self.user: AnyUser = user
        self._user_view_owner_bypass: bool = False
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: TypedInteraction) -> bool:
        return await _user_view_interaction_check(self, interaction)


class UserLayoutView(discord.ui.LayoutView):
    def __init__(self, user: AnyUser, *, timeout: float = None) -> None:
        self.user: AnyUser = user
        self._user_view_owner_bypass: bool = False
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: TypedInteraction) -> bool:
        return await _user_view_interaction_check(self, interaction)


class ConfirmationButton(discord.ui.Button['ConfirmationView']):
    def __init__(self, *, toggle: bool, **kwargs: Any) -> None:
        self.toggle: bool = toggle
        style = discord.ButtonStyle.success if toggle else discord.ButtonStyle.danger
        super().__init__(style=style, **kwargs)

    async def callback(self, interaction: TypedInteraction) -> None:
        self.view.__confirm_value__ = self.toggle
        for item in self.view.children:
            item.disabled = True
            if not isinstance(item, ConfirmationButton):
                continue

            if item.toggle is not self.toggle:
                item.style = discord.ButtonStyle.secondary

        self.view.__confirm_interaction__ = interaction
        self.view.stop()


class ConfirmationView(UserView):
    if TYPE_CHECKING:
        __confirm_value__: bool
        __confirm_interaction__: TypedInteraction

    def __init__(self, *, user: AnyUser, true: str, false: str, timeout: float = None) -> None:
        super().__init__(user, timeout=timeout)

        self.value: bool | None = None
        self.interaction: TypedInteraction | None = None

        self.add_item(ConfirmationButton(label=true, toggle=True))
        self.add_item(ConfirmationButton(label=false, toggle=False))

    @property
    def value(self) -> bool | None:
        return self.__confirm_value__

    @value.setter
    def value(self, value: bool | None) -> None:
        self.__confirm_value__ = value

    @property
    def interaction(self) -> TypedInteraction | None:
        return self.__confirm_interaction__

    @interaction.setter
    def interaction(self, interaction: TypedInteraction | None) -> None:
        self.__confirm_interaction__ = interaction


async def _dummy_parse_arguments(_ctx: Context) -> None:
    pass


async def interaction_context(command: HybridCommand | HybridGroupCommand, interaction: discord.Interaction) -> Context:
    interaction._cs_command = command
    interaction.message = None
    return await interaction.client.get_context(interaction)


async def invoke_command(
    command: HybridCommand | HybridGroupCommand,
    source: discord.Interaction | Context,
    *,
    args: Any,
    kwargs: Any,
) -> None:
    cog = command.cog
    command = command.copy()
    command.cog = cog
    ctx = await interaction_context(command, source) if isinstance(source, discord.Interaction) else source
    ctx.args = [ctx.cog, ctx, *args]
    ctx.kwargs = kwargs

    with TemporaryAttribute(ctx.command, '_parse_arguments', _dummy_parse_arguments):
        await ctx.bot.invoke(ctx)


class StaticCommandButton(discord.ui.Button):
    def __init__(
        self,
        *,
        command: Command | GroupCommand,
        command_args: list[Any] = None,
        command_kwargs: dict[str, Any] = None,
        check: Callable[[TypedInteraction], bool] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.command: HybridCommand | HybridGroupCommand = command  # type: ignore
        self.command_args = command_args or []
        self.command_kwargs = command_kwargs or {}
        self.check = check

    async def callback(self, interaction: TypedInteraction) -> Any:
        if self.check is not None and not self.check(interaction):
            return await interaction.response.send_message(
                f'You can\'t use this button, run `{self.command.qualified_name}` instead to use this command',
                ephemeral=True,
            )
        await invoke_command(self.command, interaction, args=self.command_args, kwargs=self.command_kwargs)


class CommandInvocableModal(discord.ui.Modal):
    def __init__(self, command: HybridCommand | HybridGroupCommand = None, **kwargs) -> None:
        super().__init__(timeout=300, **kwargs)
        self.command = command

    async def get_context(self, interaction: TypedInteraction) -> Context:
        return await interaction_context(self.command, interaction)

    # source is _underscored to avoid conflict with commands that have a source parameter
    async def invoke(self, _source: TypedInteraction | Context, /, *args: Any, **kwargs: Any) -> None:
        await invoke_command(self.command, _source, args=args, kwargs=kwargs)


GetModal = (
    Callable[['TypedInteraction'], Awaitable[discord.ui.Modal] | discord.ui.Modal]
    | discord.ui.Modal
)


class ModalButton(discord.ui.Button):
    def __init__(self, modal: GetModal, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.modal = modal

    async def callback(self, interaction: TypedInteraction) -> Any:
        modal = self.modal
        if callable(modal):
            modal = await discord.utils.maybe_coroutine(modal, interaction)

        await interaction.response.send_modal(modal)


class FollowUpButton(discord.ui.Button):
    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.text = text

    async def callback(self, interaction: TypedInteraction) -> None:
        await interaction.response.send_message(self.text, ephemeral=True)

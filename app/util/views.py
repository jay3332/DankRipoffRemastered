from __future__ import annotations

from typing import Any, Awaitable, Callable, TypeAlias, TYPE_CHECKING

import discord

from app.util.structures import TemporaryAttribute

if TYPE_CHECKING:
    from app.core.models import Command, Context, GroupCommand, HybridCommand, HybridGroupCommand
    from app.util.types import TypedInteraction

AnyUser: TypeAlias = discord.User | discord.Member


class OwnerBypassButton(discord.ui.Button['UserView']):
    def __init__(self, parent: UserView) -> None:
        self.parent: UserView = parent
        super().__init__(style=discord.ButtonStyle.primary, label='Enable Owner Bypass')

    async def callback(self, interaction: TypedInteraction) -> None:
        self.parent._user_view_owner_bypass = True
        await interaction.response.edit_message(content='Enabled owner bypass. You may now use the view.', view=None)


class UserView(discord.ui.View):
    def __init__(self, user: AnyUser, *, timeout: float = None) -> None:
        self.user: AnyUser = user
        self._user_view_owner_bypass: bool = False
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: TypedInteraction) -> bool:
        if interaction.user != self.user:
            if await interaction.client.is_owner(interaction.user):
                if self._user_view_owner_bypass:
                    return True

                view = discord.ui.View(timeout=30).add_item(OwnerBypassButton(self))
                await interaction.response.send_message(
                    content='Enable owner bypass to use this component view.',
                    ephemeral=True,
                    view=view,
                )
                return False

            message = f'This component view is owned by {self.user.mention}, therefore you cannot use it.'
            await interaction.response.send_message(content=message, ephemeral=True)
            return False

        return True


class ConfirmationView(UserView):
    def __init__(self, *, user: AnyUser, true: str, false: str, timeout: float = None) -> None:
        self.value: bool | None = None
        super().__init__(user, timeout=timeout)

        self._true_button = discord.ui.Button(style=discord.ButtonStyle.success, label=true)
        self._false_button = discord.ui.Button(style=discord.ButtonStyle.danger, label=false)

        self._true_button.callback = self._make_callback(True)
        self._false_button.callback = self._make_callback(False)
        self.interaction: TypedInteraction | None = None

        self.add_item(self._true_button)
        self.add_item(self._false_button)

    def _make_callback(self, toggle: bool) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(itx: discord.Interaction) -> None:
            self.value = toggle
            self._true_button.disabled = True
            self._false_button.disabled = True

            if toggle:
                self._false_button.style = discord.ButtonStyle.secondary
            else:
                self._true_button.style = discord.ButtonStyle.secondary

            self.interaction = itx
            self.stop()

        return callback


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

    async def callback(self, interaction: TypedInteraction) -> None:
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

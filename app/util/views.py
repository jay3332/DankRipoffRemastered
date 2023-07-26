from __future__ import annotations

from typing import Awaitable, Callable, TypeAlias, TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from app.util.types import TypedInteraction

AnyUser: TypeAlias = discord.User | discord.Member


class UserView(discord.ui.View):
    def __init__(self, user: AnyUser, *, timeout: float = None) -> None:
        self.user: AnyUser = user
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
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

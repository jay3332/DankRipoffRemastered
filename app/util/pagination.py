from __future__ import annotations

from abc import ABC, abstractmethod
from asyncio import Lock
from typing import Any, Generic, TYPE_CHECKING, TypeVar

from discord import ButtonStyle, Embed, Interaction
from discord.ui import Button

from app.util.views import UserView
from config import Emojis

if TYPE_CHECKING:
    from app.core.models import Context

T = TypeVar('T')

__all__ = (
    'Paginator',
    'Formatter',
)


class _PaginatorButton(Button['_PaginatorView']):
    def __init__(self, paginator: Paginator, page: int, *, emoji: str) -> None:
        page += 1
        self.paginator: Paginator = paginator
        self.page: int = page

        current = paginator.current_page + 1
        disabled = page == current or not 1 <= page <= paginator.max_pages
        label = str(page) if not disabled else None

        super().__init__(emoji=emoji, label=label, disabled=disabled)

    async def callback(self, interaction: Interaction) -> None:
        self.paginator.current_page = self.page - 1
        await self.view._update(interaction)


class _PaginatorView(UserView):
    def __init__(self, paginator: Paginator, *, timeout: float = 360) -> None:
        super().__init__(paginator.ctx.author, timeout=timeout)
        self.paginator: Paginator = paginator
        self.input_lock: Lock = Lock()

    def _get_input_button(self) -> Button:
        label = f'Page {self.paginator.current_page + 1}/{self.paginator.max_pages}'
        button = Button(style=ButtonStyle.primary, label=label)

        async def wrapper(interaction: Interaction) -> None:
            async with self.input_lock:
                button.disabled = True
                await interaction.response.edit_message(view=self)

                ctx = self.paginator.ctx
                prompt = await ctx.send('What page would you like to go to?')

                msg = await ctx.bot.wait_for(
                    'message', timeout=None, check=lambda message:
                    message.author == ctx.author and message.channel == ctx.channel
                )

                async def _fallback(content: str) -> None:
                    await ctx.send(content, delete_after=6)
                    await prompt.delete(delay=6)

                    button.disabled = False
                    await interaction.message.edit(view=self)

                try:
                    response = int(msg.content)
                except ValueError:
                    return await _fallback('Invalid page.')

                if not 1 <= response <= self.paginator.max_pages:
                    return await _fallback(f'Page number must be between 1 and {self.paginator.max_pages:,}.')

                self.paginator.current_page = response - 1

                await prompt.delete()
                ctx.bot.loop.create_task(ctx.thumbs(msg))
                await self._update(interaction)

        button.callback = wrapper
        return button

    def _update_view(self) -> None:
        self.clear_items()

        current = self.paginator.current_page
        self.add_item(_PaginatorButton(self.paginator, 0, emoji=Emojis.Arrows.first))
        self.add_item(_PaginatorButton(self.paginator, current - 1, emoji=Emojis.Arrows.previous))
        self.add_item(self._get_input_button())
        self.add_item(_PaginatorButton(self.paginator, current + 1, emoji=Emojis.Arrows.forward))
        self.add_item(_PaginatorButton(self.paginator, self.paginator.max_pages - 1, emoji=Emojis.Arrows.last))

    async def _update(self, interaction: Interaction) -> None:
        self._update_view()
        embed = await self.paginator.get_page(self.paginator.current_page)
        await interaction.message.edit(embed=embed, view=self)


class Paginator:
    def __init__(
        self,
        ctx: Context,
        formatter: Formatter,
        *,
        page: int = 0,
        timeout: float = 360,
    ) -> None:
        self.ctx: Context = ctx
        self.formatter: Formatter = formatter
        self.current_page: int = page

        self._underlying_view: _PaginatorView = _PaginatorView(self, timeout=timeout)

    @property
    def max_pages(self) -> int:
        return self.formatter.max_pages

    async def get_page(self, page: int, /) -> Embed:
        return await self.formatter.format_page(
            self, self.formatter.get_page(page)
        )

    async def start(self, *, page: int = None, **send_kwargs) -> None:
        if page is not None:
            self.current_page = page

        send_kwargs['embed'] = await self.get_page(self.current_page)

        # If there is only one page,
        # only send the embed
        if self.max_pages <= 1:
            self._underlying_view.stop()
            del self._underlying_view

            await self.ctx.send(**send_kwargs)
            return  # To abide by return annotation

        self._underlying_view._update_view()
        await self.ctx.send(view=self._underlying_view, **send_kwargs)


class Formatter(ABC, Generic[T]):
    # NOTE: Page indices start from 0, not 1,
    # add 1 to the current page for display.

    def __init__(self, entries: list[T], *, per_page: int = 1) -> None:
        assert per_page > 0
        self.entries: list[T] = entries
        self.per_page: int = per_page

    def get_page(self, page: int, /) -> list[T | list[T]]:
        if self.per_page == 1:
            return self.entries[page]

        start = self.per_page * page
        return self.entries[start:start + self.per_page]

    @property
    def max_pages(self) -> int:
        pages, extra = divmod(len(self.entries), self.per_page)
        return max(1, pages + bool(extra))

    @abstractmethod
    async def format_page(self, paginator: Paginator, entry: T | list[T]) -> Embed:
        ...


class FieldBasedFormatter(Formatter[dict[str, Any]]):
    def __init__(
        self,
        embed: Embed,
        field_kwargs: list[dict[str, Any]],
        *,
        per_page: int = 5,
    ) -> None:
        self.embed: Embed = embed
        super().__init__(field_kwargs, per_page=per_page)

    async def format_page(self, paginator: Paginator, fields: list[dict[str, Any]]) -> Embed:
        embed = self.embed.copy()
        for field in fields:
            embed.add_field(**field)

        return embed

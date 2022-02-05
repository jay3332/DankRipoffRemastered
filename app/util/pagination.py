from __future__ import annotations

from abc import ABC, abstractmethod
from asyncio import Lock
from typing import Any, Collection, Generic, TYPE_CHECKING, TypeVar

from discord import ButtonStyle, Embed, Interaction
from discord.ui import Button

from app.util.views import UserView
from config import Emojis

if TYPE_CHECKING:
    from discord.ui import Item

    from app.core.models import Context


T = TypeVar('T')

__all__ = (
    'Paginator',
    'Formatter',
)


class _PaginatorButton(Button['_PaginatorView']):
    def __init__(self, paginator: Paginator, page: int, *, emoji: str, row: int | None = None) -> None:
        page += 1
        self.paginator: Paginator = paginator
        self.page: int = page

        current = paginator.current_page + 1
        disabled = page == current or not 1 <= page <= paginator.max_pages
        label = str(page) if not disabled else None

        super().__init__(emoji=emoji, label=label, disabled=disabled, row=row)

    async def callback(self, interaction: Interaction) -> None:
        self.paginator.current_page = self.page - 1
        await self.view._update(interaction)


class PaginatorView(UserView):
    def __init__(
        self,
        paginator: Paginator,
        *,
        center_button: Button | None = None,
        other_components: Collection[Item] = None,
        row: int | None = None,
        timeout: float = 360,
    ) -> None:
        super().__init__(paginator.ctx.author, timeout=timeout)
        self.paginator: Paginator = paginator
        self.input_lock: Lock = Lock()

        self.dont_render_pagination_buttons: bool = False

        self._center_button: Button | None = center_button
        self._other_components: Collection[Item] = other_components or ()
        self._row: int | None = row

    def _get_input_button(self) -> Button:
        label = f'Page {self.paginator.current_page + 1}/{self.paginator.max_pages}'
        button = Button(style=ButtonStyle.primary, label=label, row=self._row)

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
        # Super weird implementation but it's the best I could do
        self.clear_items()

        current = self.paginator.current_page

        if self._row != 0:
            for component in self._other_components:
                self.add_item(component)

        if not self.dont_render_pagination_buttons:
            self.add_item(_PaginatorButton(self.paginator, 0, emoji=Emojis.Arrows.first, row=self._row))
            self.add_item(_PaginatorButton(self.paginator, current - 1, emoji=Emojis.Arrows.previous, row=self._row))

        if not self.dont_render_pagination_buttons or self._center_button is not None:
            self.add_item(self._center_button or self._get_input_button())

        if not self.dont_render_pagination_buttons:
            self.add_item(_PaginatorButton(self.paginator, current + 1, emoji=Emojis.Arrows.forward, row=self._row))
            self.add_item(_PaginatorButton(self.paginator, self.paginator.max_pages - 1, emoji=Emojis.Arrows.last, row=self._row))

        if self._row == 0:
            for component in self._other_components:
                self.add_item(component)

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
        center_button: Button | None = None,
        other_components: Collection[Item] = None,
        row: int | None = None,
        timeout: float = 360,
    ) -> None:
        self.ctx: Context = ctx
        self.formatter: Formatter = formatter
        self.current_page: int = page

        self._underlying_view: PaginatorView = PaginatorView(
            self, center_button=center_button, other_components=other_components, row=row, timeout=timeout,
        )

    @property
    def max_pages(self) -> int:
        return self.formatter.max_pages

    async def get_page(self, page: int, /) -> Embed:
        return await self.formatter.format_page(
            self, self.formatter.get_page(page)
        )

    async def start(self, *, edit: bool = False, page: int = None, interaction: Interaction = None, **send_kwargs) -> None:
        if page is not None:
            self.current_page = page

        send_kwargs.pop('embeds', None)
        send_kwargs['embed'] = await self.get_page(self.current_page)

        if edit:
            responder = self.ctx.maybe_edit if interaction is None else interaction.response.edit_message
        else:
            responder = self.ctx.send if interaction is None else interaction.response.send_message

        # If there is only one page,
        # only send the embed
        if self.max_pages <= 1:
            if self._underlying_view._center_button is None and not self._underlying_view._other_components:
                self._underlying_view.stop()
                del self._underlying_view

                await responder(**send_kwargs)
                return  # To abide by return annotation

            self._underlying_view.dont_render_pagination_buttons = True

        self._underlying_view._update_view()
        await responder(view=self._underlying_view, **send_kwargs)


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


class LineBasedFormatter(Formatter[str]):
    def __init__(self, embed: Embed, lines: list[str], *, per_page: int = 10, field_name: str | None = None) -> None:
        self.embed: Embed = embed
        self.field_name: str | None = field_name

        super().__init__(lines, per_page=per_page)

    async def format_page(self, paginator: Paginator, lines: list[str]) -> Embed:
        embed = self.embed.copy()

        if self.field_name is None:
            embed.description = '\n'.join(lines)
        else:
            embed.add_field(name=self.field_name, value='\n'.join(lines))

        return embed


class FieldBasedFormatter(Formatter[dict[str, Any]]):
    def __init__(
        self,
        embed: Embed,
        field_kwargs: list[dict[str, Any]],
        *,
        page_in_footer: bool = False,
        per_page: int = 5,
    ) -> None:
        self.embed: Embed = embed
        self.page_in_footer: bool = page_in_footer

        super().__init__(field_kwargs, per_page=per_page)

    async def format_page(self, paginator: Paginator, fields: list[dict[str, Any]]) -> Embed:
        embed = self.embed.copy()
        for field in fields:
            embed.add_field(**field)

        if self.page_in_footer:
            embed.set_footer(text=f'Page {paginator.current_page + 1}/{paginator.max_pages}')

        return embed

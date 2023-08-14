from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Collection, Generic, TYPE_CHECKING, TypeVar

from discord import ButtonStyle, Embed, File, Interaction
from discord.ui import Button, Modal, TextInput

from app.util.views import UserView
from config import Emojis

if TYPE_CHECKING:
    from discord.ui import Item

    from app.core.models import Context
    from app.util.types import TypedInteraction

T = TypeVar('T')
V = TypeVar('V')

__all__ = (
    'Paginator',
    'Formatter',
)


class _PaginatorButton(Button['PaginatorView']):
    def __init__(
        self, paginator: Paginator, page: int, *, emoji: str, row: int | None = None, show_label: bool = False,
    ) -> None:
        page += 1
        self.paginator: Paginator = paginator
        self.page: int = page

        current = paginator.current_page + 1
        disabled = page == current or not 1 <= page <= paginator.max_pages
        label = str(page) if not disabled and show_label else None

        super().__init__(emoji=emoji, label=label, disabled=disabled, row=row)

    async def callback(self, interaction: TypedInteraction) -> None:
        self.paginator.current_page = self.page - 1
        await self.view._finish(interaction)  # type: ignore


class _PageInputButton(Button['PaginatorView']):
    def __init__(self, paginator: Paginator, *, row: int | None = None) -> None:
        self.paginator: Paginator = paginator

        label = f'Page {paginator.current_page + 1}/{paginator.max_pages}'
        super().__init__(style=ButtonStyle.primary, label=label, row=row, disabled=self.paginator.max_pages <= 1)

    async def callback(self, interaction: TypedInteraction) -> None:
        await interaction.response.send_modal(_PageInputModal(self.view))


class _PageInputModal(Modal, title='Select Page'):
    page: TextInput = TextInput(
        label='Which page would you like to jump to?',
        placeholder='Enter a page number between 1 and %s...',
        min_length=1,
        max_length=5,
        required=True,
    )

    def __init__(self, view: PaginatorView) -> None:
        super().__init__()
        self.view: PaginatorView = view
        self.paginator: Paginator = view.paginator
        self.page.placeholder %= f'{self.paginator.max_pages:,}'

    async def on_submit(self, interaction: TypedInteraction) -> None:
        try:
            page = int(self.page.value)
        except ValueError:
            return await interaction.response.send_message(
                content='Invalid page number. (You should submit an **integer**.)',
                ephemeral=True,
            )

        if not 1 <= page <= self.paginator.max_pages:
            return await interaction.response.send_message(
                content=f'Invalid page number. (Page number should be between 1 and {self.paginator.max_pages:,}.)',
                ephemeral=True,
            )

        self.paginator.current_page = page - 1

        self.view._update_view()
        embed = await self.paginator.get_page(self.paginator.current_page)

        await interaction.response.edit_message(embed=embed, view=self.view)


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
        self.dont_render_pagination_buttons: bool = False

        self._center_button: Button | None = center_button or _PageInputButton(self.paginator, row=row)
        self._other_components: Collection[Item] = other_components or ()
        self._row: int | None = row

    def _update_view(self) -> None:
        # Super weird implementation, but it's the best I could do
        self.clear_items()

        current = self.paginator.current_page

        if self._row != 0:
            for component in self._other_components:
                self.add_item(component)

        if not self.dont_render_pagination_buttons:
            self.add_item(_PaginatorButton(self.paginator, 0, emoji=Emojis.Arrows.first, row=self._row))
            self.add_item(
                _PaginatorButton(self.paginator, current - 1, emoji=Emojis.Arrows.previous, row=self._row, show_label=True)
            )

        if not self.dont_render_pagination_buttons and isinstance(self._center_button, _PageInputButton):
            label = f'{self.paginator.current_page + 1}/{self.paginator.max_pages}'
            self._center_button.label = label

            self.add_item(self._center_button)

        elif not isinstance(self._center_button, _PageInputButton):
            self.add_item(self._center_button)

        if not self.dont_render_pagination_buttons:
            self.add_item(_PaginatorButton(self.paginator, current + 1, emoji=Emojis.Arrows.forward, row=self._row, show_label=True))
            self.add_item(
                _PaginatorButton(self.paginator, self.paginator.max_pages - 1, emoji=Emojis.Arrows.last, row=self._row)
            )

        if self._row == 0:
            for component in self._other_components:
                self.add_item(component)

    async def update(self) -> Embed | File:
        self._update_view()
        entries = self.paginator.formatter.get_page(self.paginator.current_page)
        # Update children
        for child in self.children:
            if isinstance(child, ActiveItem):
                await child.active_update(self.paginator, entries)
        # Update self
        return await self.paginator.formatter.format_page(self.paginator, entries)

    async def _finish(self, interaction: TypedInteraction) -> None:
        entity = await self.update()
        if isinstance(entity, Embed):
            await interaction.response.edit_message(embed=entity, view=self)
        elif isinstance(entity, File):
            await interaction.response.edit_message(attachments=[entity], view=self)


class Paginator:
    """An interface around a message with pages."""

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

    async def get_page(self, page: int, /) -> Embed | File:
        return await self.formatter.format_page(self, self.formatter.get_page(page))

    async def start(
        self,
        *,
        edit: bool = False,
        page: int = None,
        interaction: Interaction | TypedInteraction = None,
        **send_kwargs,
    ) -> None:
        if page is not None:
            self.current_page = page

        send_kwargs.pop('embeds', None)
        entity = await self.get_page(self.current_page)
        if isinstance(entity, Embed):
            send_kwargs['embed'] = entity
        elif isinstance(entity, File):
            send_kwargs['file'] = entity

        if interaction is not None:
            send_kwargs.pop('reference', None)
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

        await self._underlying_view.update()
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
    async def format_page(self, paginator: Paginator, entry: T | list[T]) -> Embed | File:
        raise NotImplementedError


class LineBasedFormatter(Formatter[str]):
    def __init__(
        self,
        embed: Embed,
        lines: list[str],
        *,
        per_page: int = 10,
        field_name: str | None = None,
        insert_field_at: int | None = None,
    ) -> None:
        self.embed: Embed = embed
        self.field_name: str | None = field_name
        self.insert_field_at: int | None = insert_field_at

        super().__init__(lines, per_page=per_page)

    async def format_page(self, paginator: Paginator, lines: list[str]) -> Embed | File:
        embed = Embed.from_dict(deepcopy(self.embed.to_dict()))

        if self.field_name is None:
            embed.description = '\n'.join(lines)
        else:
            kwargs = dict(name=self.field_name, value='\n'.join(lines), inline=False)
            if self.insert_field_at is None:
                embed.add_field(**kwargs)
            else:
                embed.insert_field_at(self.insert_field_at, **kwargs)

        return embed


class FieldBasedFormatter(Formatter[dict[str, V]]):
    def __init__(
        self,
        embed: Embed,
        field_kwargs: list[dict[str, V]],
        *,
        page_in_footer: bool = False,
        per_page: int = 5,
    ) -> None:
        self.embed: Embed = embed
        self.page_in_footer: bool = page_in_footer

        super().__init__(field_kwargs, per_page=per_page)

    async def format_page(self, paginator: Paginator, fields: list[dict[str, V]]) -> Embed | File:
        embed = Embed.from_dict(deepcopy(self.embed.to_dict()))
        for field in fields:
            embed.add_field(**field)

        if self.page_in_footer:
            embed.set_footer(text=f'Page {paginator.current_page + 1}/{paginator.max_pages}')

        return embed


class ActiveItem(Generic[T]):
    """Mixin to allow updating a component when a paginator updates."""

    async def active_update(self, paginator: Paginator, entry: T | list[T]) -> None:
        """Callback for when the paginator updates."""
        pass

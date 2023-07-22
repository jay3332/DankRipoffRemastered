from __future__ import annotations

import warnings
from enum import IntEnum
from typing import NamedTuple, TYPE_CHECKING, TypeAlias

from app.util.common import sentinel

if TYPE_CHECKING:
    from app.core import Context

    AnsiIdentifierKwargs: TypeAlias = 'AnsiColor | AnsiBackgroundColor | bool'

INHERIT = sentinel('INHERIT', repr='INHERIT')


class AnsiColor(IntEnum):
    default = 39
    gray = 30
    red = 31
    green = 32
    yellow = 33
    blue = 34
    magenta = 35
    cyan = 36
    white = 37


class AnsiStyle(IntEnum):
    default = 0
    bold = 1
    underline = 4


class AnsiBackgroundColor(IntEnum):
    default = 49
    black = 40
    red = 41
    gray = 42
    light_gray = 43
    lighter_gray = 44
    blurple = 45
    lightest_gray = 46
    white = 47


class AnsiChunk(NamedTuple):
    text: str
    color: AnsiColor = INHERIT
    background_color: AnsiBackgroundColor = INHERIT
    bold: bool = INHERIT
    underline: bool = INHERIT

    def with_text(self, text: str, /) -> AnsiChunk:
        return AnsiChunk(
            text=text,
            color=self.color,
            bold=self.bold,
            underline=self.underline,
            background_color=self.background_color,
        )

    def to_dict(self) -> dict[str, AnsiIdentifierKwargs]:
        result = {}
        if self.color is not INHERIT:
            result['color'] = self.color

        if self.background_color is not INHERIT:
            result['background_color'] = self.background_color

        if self.bold is not INHERIT:
            result['bold'] = self.bold

        if self.underline is not INHERIT:
            result['underline'] = self.underline

        return result

    @classmethod
    def reset(cls) -> AnsiChunk:
        return cls(
            text='',
            color=AnsiColor.default,
            background_color=AnsiBackgroundColor.default,
            bold=False,
            underline=False,
        )


class AnsiStringBuilder:
    """Aids in building an ANSI string."""

    __slots__ = (
        '_chunks',
        '_prefix',
        '_fallback_prefix',
        '_suffix',
        '_default_color',
        '_default_bold',
        '_default_underline',
        '_default_background_color',
    )

    if TYPE_CHECKING:
        _chunks: list[AnsiChunk]
        _prefix: str
        _fallback_prefix: str
        _suffix: str
        _default_color: AnsiColor | None
        _default_bold: bool
        _default_underline: bool
        _default_background_color: AnsiBackgroundColor | None

    def __init__(self, string: str = '') -> None:
        self._chunks: list[AnsiChunk] = []
        self._prefix = self._fallback_prefix = self._suffix = ''

        if string:
            self._chunks.append(AnsiChunk(string))

        self._default_color = self._default_background_color = None
        self._default_bold = self._default_underline = False

    @property
    def previous(self) -> AnsiChunk:
        return self._chunks[-1] if self._chunks else AnsiChunk('')

    @property
    def previous_color(self) -> AnsiColor:
        for chunk in reversed(self._chunks):
            if chunk.color is not INHERIT:
                return chunk.color

        return AnsiColor.default

    @property
    def previous_background_color(self) -> AnsiBackgroundColor:
        for chunk in reversed(self._chunks):
            if chunk.background_color is not INHERIT:
                return chunk.background_color

        return AnsiBackgroundColor.default

    @property
    def previous_bold(self) -> bool:
        for chunk in reversed(self._chunks):
            if chunk.bold is not INHERIT:
                return chunk.bold

        return self._default_bold

    @property
    def previous_underline(self) -> bool:
        for chunk in reversed(self._chunks):
            if chunk.underline is not INHERIT:
                return chunk.underline

        return self._default_underline

    @property
    def base_length(self) -> int:
        return sum(len(chunk.text) for chunk in self._chunks)

    @property
    def raw_length(self) -> int:
        return self.base_length + len(self._fallback_prefix) + len(self._suffix)

    @property
    def raw(self) -> str:
        """The raw, unformatted content of this string."""
        return self._fallback_prefix + ''.join(chunk.text for chunk in self._chunks) + self._suffix

    def append(
        self,
        text: str,
        *,
        inherit: bool = False,
        color: AnsiColor = None,
        bold: bool = None,
        underline: bool = None,
        background_color: AnsiBackgroundColor = None,
    ) -> AnsiStringBuilder:
        """Append a chunk of text to the builder."""
        if color is None:
            if self._default_color is not None:
                color = self._default_color
            else:
                color = self.previous_color if inherit else AnsiColor.default

        if bold is None:
            if self._default_bold is not None:
                bold = self._default_bold
            else:
                bold = self.previous_bold if inherit else False

        if underline is None:
            if self._default_underline is not None:
                underline = self._default_underline
            else:
                underline = self.previous_underline if inherit else False

        if background_color is None:
            if self._default_background_color is not None:
                background_color = self._default_background_color
            else:
                background_color = self.previous_background_color if inherit else AnsiBackgroundColor.default

        self._chunks.append(
            AnsiChunk(text, color=color, background_color=background_color, bold=bold, underline=underline),
        )
        return self

    def extend(self, other: AnsiStringBuilder) -> AnsiStringBuilder:
        """Extend the builder with another builder."""
        self._chunks.extend(other._chunks)
        return self

    def strip(self) -> AnsiStringBuilder:
        """Strips spaces and newlines from the beginning and end of the string."""
        if not self._chunks:
            return self

        elif len(self._chunks) == 1:
            chunk = self._chunks[0]
            self._chunks[0] = chunk.with_text(chunk.text.strip())
            return self

        first, *_, last = self._chunks
        self._chunks[0] = first.with_text(first.text.lstrip())
        self._chunks[-1] = last.with_text(last.text.rstrip())

        return self

    def newline(self, count: int = 1) -> AnsiStringBuilder:
        """Appends a newline to the string."""
        return self.append('\n' * count)

    def bold(self, text: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text in bold."""
        self._default_bold = True
        if text:
            self.append(text, bold=True, **kwargs)

        return self

    def no_bold(self, text: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text without bold."""
        self._default_bold = False
        if text:
            self.append(text, bold=False, **kwargs)

        return self

    def underline(self, text: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text in underline."""
        self._default_underline = True
        if text:
            self.append(text, underline=True, **kwargs)

        return self

    def no_underline(self, text: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text without underline."""
        self._default_underline = False
        if text:
            self.append(text, underline=False, **kwargs)

        return self

    def color(self, color: AnsiColor, text: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text in the given color."""
        self._default_color = color
        if text:
            self.append(text, color=color, **kwargs)

        return self

    def no_color(self, text: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends and persists text without color."""
        self._default_color = None
        if text:
            self.append(text, color=None, **kwargs)

        return self

    def background_color(
        self,
        background_color: AnsiBackgroundColor,
        text: str = '',
        **kwargs: AnsiIdentifierKwargs,
    ) -> AnsiStringBuilder:
        """Appends text in the given background color."""
        self._default_background_color = background_color
        if text:
            self.append(text, background_color=background_color, **kwargs)

        return self

    def no_background_color(self, text: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Appends text without background color."""
        self._default_background_color = None
        if text:
            self.append(text, background_color=None, **kwargs)

        return self

    def clear_formatting(self) -> AnsiStringBuilder:
        """Clears all default formatting."""
        self._default_color = self._default_background_color = None
        self._default_bold = self._default_underline = False
        self._chunks.append(AnsiChunk(''))

        return self

    def ensure_codeblock(self, *, fallback: str = '') -> AnsiStringBuilder:
        """Ensures that the string is wrapped in a codeblock."""
        raw = self.raw
        if raw.startswith('```') and raw.endswith('```'):
            return self

        self._prefix = '```ansi\n'
        self._fallback_prefix = f'```{fallback}\n'
        self._suffix = '```'

        return self

    def merge_chunks(self) -> AnsiStringBuilder:
        """Merges compatible chunks into one.

        Chunks are compatible if any of the following conditions are met:
        - the second chunk has everything set to INHERIT
        - the two do not have conflicting parts, e.g. one has a color and the second one only has background_color

        If the first one is blank, overwrite second with first.
        """
        chunks: list[AnsiChunk | None] = self._chunks

        for i, chunk in enumerate(chunks, -1):
            previous = chunks[i] if i >= 0 else None

            if not previous:
                continue

            if all(entity is INHERIT for entity in (
                chunk.color,
                chunk.background_color,
                chunk.bold,
                chunk.underline,
            )):
                chunks[i] = previous.with_text(previous.text + chunk.text)
                chunks[i + 1] = None
                continue

            chunk_dict = chunk.to_dict()
            previous_dict = previous.to_dict()

            # Equal keys cancel out
            for key in chunk_dict.copy():
                if key in previous_dict and chunk_dict[key] == previous_dict[key]:
                    del chunk_dict[key]
                    del previous_dict[key]

            # If the keys don't conflict they are compatible
            if set(chunk_dict).isdisjoint(previous_dict):
                chunks[i] = previous.with_text(previous.text + chunk.text)
                chunks[i + 1] = None

            # Merge the two if the first one is blank
            if not previous.text:
                previous_dict.update(chunk_dict)
                chunks[i] = AnsiChunk(chunk.text, **previous_dict)
                chunks[i + 1] = None

        self._chunks = [chunk for chunk in chunks if chunk is not None]
        return self

    def build(self) -> str:  # sourcery no-metrics
        """Builds the string."""
        previous_color = previous_background_color = previous_bold = previous_underline = None
        result = []

        self.merge_chunks()

        for chunk in self._chunks:
            specs = []

            if chunk.color is not INHERIT and chunk.color != previous_color:
                previous_color = chunk.color
                specs.append(chunk.color)

            if chunk.background_color is not INHERIT and chunk.background_color != previous_background_color:
                previous_background_color = chunk.background_color
                specs.append(chunk.background_color)

            reset = False

            if chunk.bold is not INHERIT and chunk.bold is not previous_bold:
                previous_bold = chunk.bold
                if chunk.bold:
                    specs.append(AnsiStyle.bold)
                else:
                    reset = True

            if chunk.underline is not INHERIT and chunk.underline is not previous_underline:
                previous_underline = chunk.underline
                if chunk.underline:
                    specs.append(AnsiStyle.underline)
                else:
                    reset = True

            if reset:
                for entity in (
                    previous_color,
                    previous_background_color,
                    AnsiStyle.bold if previous_bold else None,
                    AnsiStyle.underline if previous_underline else None,
                ):
                    if entity is not None and entity not in specs:
                        specs.append(entity)

                specs = [entity for entity in specs if entity.name != 'default']
                specs.insert(0, AnsiStyle.default)

            if specs:
                specs = ';'.join(str(spec.value) for spec in specs)
                result.append(f'\x1b[{specs}m')

            result.append(chunk.text)

        return self._prefix + ''.join(result) + self._suffix

    def dynamic(self, _ctx: Context) -> str:
        """Returns the built string only if the user of the given context is not on mobile."""
        warnings.warn(DeprecationWarning('discord properly handles ansi on mobile now, use .build() instead'))
        return self.build()

    def __str__(self) -> str:
        return self.build()

    def __repr__(self) -> str:
        return f'<AnsiStringBuilder len={len(self)}>'

    def __len__(self) -> int:
        return len(self.build())

    def __iadd__(self, other: AnsiStringBuilder | str) -> AnsiStringBuilder:
        if isinstance(other, AnsiStringBuilder):
            return self.extend(other)

        return self.append(other)

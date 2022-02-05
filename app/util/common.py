from __future__ import annotations

import math
import random
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Iterator, Optional, TYPE_CHECKING, Type, TypeVar

from discord.ext.commands import Converter

from config import Emojis

if TYPE_CHECKING:
    from app.core import Context

    Q = TypeVar('Q')
    T = TypeVar('T')

__all__ = (
    'setinel',
    'level_requirement_for',
    'calculate_level',
    'converter',
)

EMOJI_REGEX: re.Pattern[str] = re.compile(r'<(a)?:([a-zA-Z0-9_]{2,32}):([0-9]{17,25})>')
PLURALIZE_REGEX: re.Pattern[str] = re.compile(r'(?P<quantity>-?[0-9.,]+) (?P<thing>[a-zA-Z]+)\((?P<plural>i?e?s)\)')


# This exists for type checkers
class ConstantT:
    pass


def setinel(name: str, **dunders) -> ConstantT:
    attrs = {f'__{k}__': lambda _: v for k, v in dunders.items()}
    return type(name, (ConstantT,), attrs)()


def converter(f: Callable[[Context, str], T]) -> Type[Converter | T]:
    class Wrapper(Converter):
        async def convert(self, ctx: Context, argument: str) -> T:
            return await f(ctx, argument)

    return Wrapper


def level_requirement_for(level: int, /, *, base: int = 1000, factor: float = 1.45) -> int:
    precise = base * factor ** level
    return math.ceil(precise / 100) * 100


def calculate_level(exp: int, *, base: int = 1000, factor: float = 1.45) -> tuple[int, int, int]:
    kwargs = {'base': base, 'factor': factor}
    level = 0

    while exp > (requirement := level_requirement_for(level, **kwargs)):
        exp -= requirement
        level += 1

    return level, exp, requirement


def image_url_from_emoji(emoji: str) -> str:
    if match := EMOJI_REGEX.match(emoji):
        animated, _, id = match.groups()
        extension = 'gif' if animated else 'png'
        return f'https://cdn.discordapp.com/emojis/{id}.{extension}?v=1'
    else:
        code = format(ord(emoji[0]), 'x')
        return f'https://twemoji.maxcdn.com/v/latest/72x72/{code}.png'


def walk_collection(collection: type, cls: Type[Q]) -> Iterator[Q]:
    for attr in dir(collection):
        if attr.startswith('_'):
            continue

        obj = getattr(collection, attr)
        if not isinstance(obj, cls):
            continue

        yield obj


def get_by_key(collection: type, key: str) -> Any:
    for attr in dir(collection):
        if attr.startswith('_'):
            continue

        obj = getattr(collection, attr)
        if hasattr(obj, 'key') and obj.key == key:
            return obj


def query_collection(collection: type, cls: Type[Q], query: str) -> Optional[Q]:
    query = query.lower()
    queued = []

    for obj in walk_collection(collection, cls):
        query = query.lower()
        name = obj.name.lower()

        if query == name:
            return obj

        if len(query) >= 3 and query in name:
            queued.append(obj)

        matcher = SequenceMatcher(None, query, name)
        if matcher.ratio() > .85 and all(digit not in query for digit in '0123456789'):
            queued.append(obj)

    if queued:
        return min(queued, key=lambda item: len(item.key))


def cutoff(string: str, /, max_length: int = 64, *, exact: bool = False) -> str:
    """Cuts-off a string at a certain length, and if it has been cutoff, append "..." to it."""
    if len(string) <= max_length:
        return string

    offset = 0 if not exact else 3
    return string[:max_length - offset] + '...'


def pluralize(text: str, /) -> str:
    """Automatically finds words that need to be pluralized in a string and pluralizes it."""
    def callback(match):
        quantity = abs(float((q := match.group('quantity')).replace(',', '')))
        return f'{q} ' + match.group('thing') + (('', match.group('plural'))[quantity != 1])

    return PLURALIZE_REGEX.sub(callback, text)


def humanize_list(li: list[Any]) -> str:
    """Takes a list and returns it joined."""
    if len(li) <= 2:
        return " and ".join(li)

    return ", ".join(li[:-1]) + f", and {li[-1]}"


def humanize_small_duration(seconds: float, /) -> str:
    """Turns a very small duration into a human-readable string."""
    units = ('ms', 'μs', 'ns', 'ps')

    for i, unit in enumerate(units, start=1):
        boundary = 10 ** 3 * i

        if seconds > 1 / boundary:
            m = seconds * boundary
            m = round(m, 2) if m >= 10 else round(m, 3)

            return f"{m} {unit}"

    return "<1 ps"


def humanize_duration(seconds, depth: int = 3):
    """Formats a duration (in seconds) into one that is human-readable."""
    if seconds < 1:
        return '<1 second'

    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    mo, d = divmod(d, 30)
    y, mo = divmod(mo, 12)

    if y > 100:
        return ">100 years"

    y, mo, d, h, m, s = [int(entity) for entity in (y, mo, d, h, m, s)]
    items = (y, 'year'), (mo, 'month'), (d, 'day'), (h, 'hour'), (m, 'minute'), (s, 'second')

    as_list = [f"{quantity} {unit}{'s' if quantity != 1 else ''}" for quantity, unit in items if quantity > 0]
    return humanize_list(as_list[:depth])


def insert_random_u200b(text: str, /) -> str:
    """Inserts random zero-width space characters into a string, usually to make them copy-paste proof."""
    return ''.join(c + random.randint(0, 4) * '\u200b' for c in text)


def progress_bar(ratio: float, *, length: int = 8, u200b: bool = True) -> str:
    # noinspection PyTypeChecker
    ratio = min(1, max(0, ratio))

    result = ''
    span = 1 / length

    # Pre-calculate spans
    quarter_span = span / 4
    half_span = span / 2
    high_span = 3 * quarter_span

    for i in range(length):
        lower = i / length

        if ratio <= lower:
            key = 'empty'
        elif ratio <= lower + quarter_span:
            key = 'low'
        elif ratio <= lower + half_span:
            key = 'mid'
        elif ratio <= lower + high_span:
            key = 'high'
        else:
            key = 'full'

        if i == 0:
            start = 'left'
        elif i == length - 1:
            start = 'right'
        else:
            start = 'mid'

        result += getattr(Emojis.ProgressBars, f'{start}_{key}')

    if u200b:
        return result + "\u200b"

    return result

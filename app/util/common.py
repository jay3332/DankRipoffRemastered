from __future__ import annotations

import math
from typing import Callable, TYPE_CHECKING, Type, TypeVar

from discord.ext.commands import Converter

if TYPE_CHECKING:
    from app.core import Context

    T = TypeVar('T')

__all__ = (
    'setinel',
    'level_requirement_for',
    'calculate_level',
    'converter',
)


# This exists for type checkers
class ConstantT:
    pass


def setinel(name: str, **dunders) -> ConstantT:
    attrs = {f'__{k}__': lambda _: v for k, v in dunders.items()}
    return type(name, (ConstantT,), attrs)()


def converter(f: Callable[[Context, str], T]) -> Type[Converter]:
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

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Generic, Self, TypeVar

T = TypeVar('T')
V = TypeVar('V')


class Timer:
    def __init__(self) -> None:
        self.start_time: float | None = None
        self.end_time: float | None = None

    def __enter__(self) -> Self:
        self.start_time = perf_counter()
        return self

    def __exit__(self, _type, _val, _tb) -> None:
        self.end_time = perf_counter()

    @property
    def time(self) -> float:
        if not (self.start_time and self.end_time):
            raise ValueError('timer has not been stopped')

        return self.end_time - self.start_time

    def __repr__(self) -> str:
        return f'<Timer time={self.time}>'

    def __int__(self) -> int:
        return int(self.time)

    def __float__(self) -> float:
        return self.time


class LockReasonMonitor:
    def __init__(self, lock: LockWithReason, reason: str | None) -> None:
        self._lock: LockWithReason = lock
        self.reason: str | None = reason

    async def __aenter__(self) -> None:
        self._lock.set_reason(self.reason)
        await self._lock.__aenter__()

    async def __aexit__(self, *args) -> None:
        self._lock.reason = None
        await self._lock.__aexit__(*args)


class LockWithReason(asyncio.Lock):
    def __init__(self, reason: str | None = None, *, jump_url: str | None = None) -> None:
        super().__init__()
        self.reason: str | None = reason
        self.jump_url: str | None = jump_url

    def set_reason(self, reason: str) -> None:
        self.reason = reason

    def with_reason(self, reason: str | None) -> LockReasonMonitor:
        return LockReasonMonitor(self, reason)


_ATTR_MISSING = object()


class TemporaryAttribute(Generic[T, V]):
    __slots__ = ('obj', 'attr', 'value', 'original')

    def __init__(self, obj: T, attr: str, value: V) -> None:
        self.obj: T = obj
        self.attr: str = attr
        self.value: V = value
        self.original: V = getattr(obj, attr, _ATTR_MISSING)

    def __enter__(self) -> T:
        setattr(self.obj, self.attr, self.value)
        return self.obj

    def __exit__(self, _type, _val, _tb) -> None:
        if self.original is _ATTR_MISSING:
            return delattr(self.obj, self.attr)

        setattr(self.obj, self.attr, self.original)


class DottedDict(dict[str, V]):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

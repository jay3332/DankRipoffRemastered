import asyncio
from time import perf_counter
from typing import TypeVar

T = TypeVar('T', bound='Timer')

__all__ = (
    'Timer',
)


class Timer:
    def __init__(self) -> None:
        self.start_time: float | None = None
        self.end_time: float | None = None

    def __enter__(self: T) -> T:
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


class LockWithReason(asyncio.Lock):
    def __init__(self, reason: str | None = None) -> None:
        super().__init__()
        self.reason: str | None = reason

    def set_reason(self, reason: str) -> None:
        self.reason = reason

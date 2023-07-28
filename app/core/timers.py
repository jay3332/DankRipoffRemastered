from __future__ import annotations

import asyncio
import datetime
import json
from asyncpg import PostgresConnectionError, Record
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Callable,
    ClassVar,
    Concatenate,
    Iterable,
    Literal,
    ParamSpec,
    TYPE_CHECKING,
    Type,
    TypeVar,
)

import discord
from discord.utils import format_dt, utcnow

if TYPE_CHECKING:
    from app.core import Bot
    from app.database import Database

    P = ParamSpec('P')
    TimerT = TypeVar('TimerT', bound='Timer')


@dataclass
class Timer:
    """Represents a timer record."""
    id: int
    event: str
    created_at: datetime.datetime
    expires: datetime.datetime
    metadata: Any | None
    manager: TimerManager | None = None
    finished: bool = False

    @classmethod
    def from_record(cls: Type[TimerT], record: Record, *, manager: TimerManager | None = None) -> TimerT:
        """Construct a new Timer from a database record."""
        return cls(
            id=record['id'],
            event=record['event'],
            created_at=record['created_at'],
            expires=record['expires'],
            metadata=record['metadata'] and json.loads(record['metadata']),
            manager=manager,
        )

    async def end(self, *, manager: TimerManager | None = None) -> None:
        """Ends this timer early if it is manager aware."""
        manager = manager or self.manager
        if manager is None:
            raise TypeError("this timer is not aware of it's parent TimerManager")

        await manager.end_timer(self)

    def is_short_dispatch(self) -> bool:
        """Returns whether this timer should be dispatched as a short timer."""
        return self.id < 0

    def as_discord_timestamp(self, spec: Literal['f', 'F', 'd', 'D', 't', 'T', 'R'] = 'R') -> str:
        """Return this timer formatted as <t:expires:spec>."""
        return format_dt(self.expires, spec)

    def __repr__(self) -> str:
        return f'<Timer id={self.id} event={self.event!r} expires={self.expires!r} finished={self.finished}>'

    def __eq__(self: TimerT, other: TimerT) -> bool:
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


class TimerManager:
    """Manages and dispatches all event timers."""

    SHORT_TIMER_THRESHOLD: ClassVar[int] = 30  # highest time in seconds of when to just use asyncio.sleep
    MAX_DAYS: ClassVar[int] = 40

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.db: Database = bot.db

        self._current: Timer | None = None
        self._dispatch: Callable[Concatenate[str, P], None] = bot.dispatch
        self._loop: asyncio.AbstractEventLoop = bot.loop
        self._short_timers: dict[int, Timer] = {}

        self.__task: asyncio.Task = self._loop.create_task(self.start())
        self.__event: asyncio.Event = asyncio.Event()

        self.__short_timer_key_buffer: int = 0
        self.__short_timer_key_mutex: asyncio.Lock = asyncio.Lock()

    @property
    def current(self) -> Timer | None:
        """The current timer this manager is waiting for completion of.

        If no timers exist then this will be ``None``.
        """
        return self._current

    @property
    def short_timers(self) -> Iterable[Timer]:
        """Returns an iterable of all short timers."""
        return self._short_timers.values()

    def reset_task(self) -> None:
        self.__task.cancel()
        self.__task = self._loop.create_task(self.start())

    async def get_timer(self, id: int) -> Timer | None:
        """Get a timer by its ID, or return ``None`` if it isn't found."""
        if id < 0:
            return self._short_timers.get(id)

        query = 'SELECT * FROM timers WHERE id = $1'
        record = await self.db.fetchrow(query, id)

        return record and Timer.from_record(record, manager=self)

    async def walk_timers(
        self,
        *,
        event: str | None = None,
        where: str | None = None,
        predicate: Callable[[Timer], bool] | None = None,
        args: Iterable[Any] = (),
    ) -> AsyncIterator[Timer]:
        """Iterate over all timers that are of the given event."""
        args = (*args, event) if event else args
        clause = f'event = ${len(args)} AND' if event else ''

        query = f'SELECT * FROM timers WHERE {clause} {where or True}'

        for timer in self._short_timers.values():
            if event and timer.event != event:
                continue

            if predicate is None or predicate(timer):
                yield timer

        for record in await self.db.fetch(query, *args):
            timer = Timer.from_record(record, manager=self)

            if predicate is None or predicate(timer):
                yield timer

    async def next_timer(self, *, max_days: int = 7) -> Timer | None:
        """Fetches the timer that expires the soonest from the database.

        If no timers expire in at most ``max_days`` days, then ``None`` is returned instead.
        """
        query = """
                SELECT
                    *
                FROM
                    timers
                WHERE
                    expires < (CURRENT_DATE + $1::interval)
                ORDER BY
                    expires
                LIMIT
                    1
                """
        record = await self.db.fetchrow(query, datetime.timedelta(days=max_days))
        return record and Timer.from_record(record, manager=self)

    async def wait(self, *, max_days: int = 7) -> Timer:
        """Waits for the next timer to be available."""
        if timer := await self.next_timer(max_days=max_days):
            self.__event.set()
            return timer

        self.__event.clear()
        self._current = None

        await self.__event.wait()
        return await self.next_timer(max_days=max_days)

    async def _start_dispatch(self) -> None:
        await self.db.wait()

        while not self.bot.is_closed():
            timer = self._current = await self.wait(max_days=self.MAX_DAYS)
            now = utcnow()

            if timer.expires > now:
                delta = timer.expires - now
                await asyncio.sleep(delta.total_seconds())

            await self.end_timer(timer)

    async def start(self) -> None:
        """Starts waiting and dispatching timers."""
        try:
            await self._start_dispatch()
        except asyncio.CancelledError:
            raise
        except (OSError, discord.ConnectionClosed, PostgresConnectionError):
            self.reset_task()

    async def start_short_timer(self, seconds: float, timer: Timer) -> None:
        """Simply sleeps until the timer expires."""
        await asyncio.sleep(seconds)
        await self.end_timer(timer)

    async def end_timer(self, timer: Timer, *, dispatch: bool = True, cascade: bool = False) -> None:
        """Ends and deletes the specified timer."""
        if timer.is_short_dispatch():
            del self._short_timers[timer.id]
        else:
            await self.db.execute('DELETE FROM timers WHERE id = $1', timer.id)

        if cascade and self.current and self.current == timer:
            self.reset_task()

        if dispatch:
            await self.dispatch_finished_timer(timer)

    async def dispatch_finished_timer(self, timer: Timer) -> None:
        """Dispatches the finished timer."""
        if timer.finished:
            return

        self._dispatch(f'{timer.event}_timer_complete', timer)
        timer.finished = True

    async def decrement_atomic_key(self) -> int:
        """Decrements the atomic key buffer used to store short timers atomically."""
        async with self.__short_timer_key_mutex:
            self.__short_timer_key_buffer -= 1
            return self.__short_timer_key_buffer

    async def create(self, when: datetime.datetime | datetime.timedelta | int, event: str, **metadata) -> Timer:
        """Creates and serves a new timer."""
        now = utcnow()

        if isinstance(when, int):
            when = datetime.timedelta(seconds=when)

        if isinstance(when, datetime.timedelta):
            when = now + when

        timer = Timer(
            id=0,  # id is guaranteed to be changed
            event=event,
            expires=when,
            created_at=now,
            metadata=metadata,
            manager=self,
        )

        seconds = (when - now).total_seconds()
        if seconds < self.SHORT_TIMER_THRESHOLD:
            key = await self.decrement_atomic_key()
            self._short_timers[key] = timer
            timer.id = key

            self._loop.create_task(self.start_short_timer(seconds, timer))
            return timer

        query = """
                INSERT INTO timers (
                    event, expires, created_at, metadata
                )
                VALUES (
                    $1, $2, $3, $4
                )
                RETURNING
                    id
                """
        timer.id = await self.db.fetchval(query, event, when, now, json.dumps(metadata))

        if seconds <= self.MAX_DAYS * 86400:
            self.__event.set()

        if self._current and when < self._current.expires:
            self.reset_task()

        return timer

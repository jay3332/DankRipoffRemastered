from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, Literal, overload

import asyncpg

from app.util.common import calculate_level
from config import DatabaseConfig, beta
from .migrations import Migrator

__all__ = (
    'Database',
    'Migrator',
)


class _Database:
    _internal_pool: asyncpg.Pool

    def __init__(self, *, loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self.loop.create_task(self._connect())

    async def _connect(self) -> None:
        self._internal_pool = await asyncpg.create_pool(
            host=DatabaseConfig.host,
            port=DatabaseConfig.port,
            user=DatabaseConfig.user,
            database=DatabaseConfig.name,
            password=DatabaseConfig.beta_password if beta else DatabaseConfig.password
        )

        async with self.acquire() as conn:
            migrator = Migrator(conn)
            await migrator.run_migrations()

    @overload
    def acquire(self, *, timeout: float = None) -> Awaitable[asyncpg.Connection]:
        ...

    def acquire(self, *, timeout: float = None) -> asyncpg.pool.PoolAcquireContext:
        return self._internal_pool.acquire(timeout=timeout)

    def execute(self, query: str, *args: Any, timeout: float = None) -> Awaitable[str]:
        return self._internal_pool.execute(query, *args, timeout=timeout)

    def fetch(self, query: str, *args: Any, timeout: float = None) -> Awaitable[list[asyncpg.Record]]:
        return self._internal_pool.fetch(query, *args, timeout=timeout)

    def fetchrow(self, query: str, *args: Any, timeout: float = None) -> Awaitable[asyncpg.Record]:
        return self._internal_pool.fetchrow(query, *args, timeout=timeout)

    def fetchval(self, query: str, *args: Any, column: str | int = 0, timeout: float = None) -> Awaitable[Any]:
        return self._internal_pool.fetchval(query, *args, column=column, timeout=timeout)


class Database(_Database):
    """Manages transactions to and from the database.

    Additionally, this is where you will find the cache which stores records to be used later.
    """

    def __init__(self, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(loop=loop)
        self.user_records: dict[int, UserRecord] = {}

    @overload
    def get_user_record(self, user_id: int, *, fetch: Literal[True] = True) -> Awaitable[UserRecord]:
        ...

    @overload
    def get_user_record(self, user_id: int, *, fetch: Literal[False] = True) -> UserRecord:
        ...

    def get_user_record(self, user_id: int, *, fetch: bool = True):
        try:
            record = self.user_records[user_id]
        except KeyError:
            record = self.user_records[user_id] = UserRecord(user_id, db=self)

        if not fetch:
            return record

        return record.fetch_if_necessary()


class UserRecord:
    """Stores data about a user."""

    LEVELING_CURVE = dict(base=100, factor=1.26)

    def __init__(self, user_id: int, *, db: Database) -> None:
        self.db: Database = db
        self.user_id: int = user_id
        self.data: dict[str, Any] = {}

    async def fetch(self) -> UserRecord:
        query = """
                INSERT INTO users (user_id) VALUES ($1) 
                ON CONFLICT (user_id) DO UPDATE SET user_id = $1
                RETURNING *;
                """

        self.data.update(await self.db.fetchrow(query, self.user_id))  # TODO: Welcome user if new
        return self

    async def fetch_if_necessary(self) -> UserRecord:
        if not len(self.data):
            await self.fetch()

        return self

    async def _update(self, key: Callable[[tuple[int, str]], str], values: dict[str, Any]) -> UserRecord:
        query = """
                UPDATE users SET {} WHERE user_id = $1
                RETURNING *;
                """

        # noinspection PyTypeChecker
        self.data.update(
            await self.db.fetchrow(
                query.format(', '.join(map(key, enumerate(values.keys(), start=2)))),
                self.user_id,
                *values.values(),
            ),
        )
        return self

    def update(self, **values: Any) -> Awaitable[UserRecord]:
        return self._update(lambda o: f'"{o[1]}" = ${o[0]}', values)

    def add(self, **values: Any) -> Awaitable[UserRecord]:
        return self._update(lambda o: f'"{o[1]}" = "{o[1]}" + ${o[0]}', values)

    async def add_coins(self, coins: int, /) -> int:
        """Adds coins including applying multipliers. Returns the amount of coins added."""
        await self.add(wallet=coins)
        return coins

    async def add_exp(self, exp: int, /) -> bool:
        """Return whether or not the user as leveled up."""
        old = self.level
        await self.add(exp=exp)
        return self.level > old  # TODO: Notify user

    async def add_random_bank_space(self, minimum: int, maximum: int, *, chance: float = 1) -> int:
        if random.random() > chance:
            return 0

        await self.add(max_bank=(amount := random.randint(minimum, maximum)))
        return amount

    async def add_random_exp(self, minimum: int, maximum: int, *, chance: float = 1) -> int:
        if random.random() > chance:
            return 0

        await self.add_exp(amount := random.randint(minimum, maximum))
        return amount

    @property
    def wallet(self) -> int:
        return self.data['wallet']

    @property
    def bank(self) -> int:
        return self.data['bank']

    @property
    def max_bank(self) -> int:
        return self.data['max_bank']

    @property
    def bank_ratio(self) -> float:
        return self.bank / self.max_bank

    @property
    def total_coins(self) -> int:
        return self.wallet + self.bank

    @property
    def total_exp(self) -> int:
        return self.data['exp']

    @property
    def level_data(self) -> tuple[int, int, int]:
        return calculate_level(self.total_exp, **self.LEVELING_CURVE)

    @property
    def level(self) -> int:
        return self.level_data[0]

    @property
    def exp(self) -> int:
        return self.level_data[1]

    @property
    def exp_requirement(self) -> int:
        return self.level_data[2]

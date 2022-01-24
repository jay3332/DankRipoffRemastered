from __future__ import annotations

import asyncio
import datetime
import random
from typing import Any, Awaitable, Callable, Literal, NamedTuple, overload

import asyncpg

from app.data.items import Item, Items
from app.util.common import calculate_level, get_by_key
from config import DatabaseConfig, Emojis, beta
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


class InventoryMapping(dict[Item, int]):
    def quantity_of(self, item: Item | str) -> int:
        try:
            return self[item]
        except KeyError:
            return 0

    def __getitem__(self, item: Item | str) -> int:
        if isinstance(item, str):
            item = get_by_key(Items, item)

        if item is None:
            raise RuntimeError(f'Item {item!r} does not exist')

        return super().__getitem__(item)

    def __setitem__(self, item: Item | str, value: int) -> None:
        if isinstance(item, str):
            item = get_by_key(Items, item)

        if item is None:
            return

        return super().__setitem__(item, value)

    def __contains__(self, item: Item | str) -> bool:
        if isinstance(item, str):
            item = get_by_key(Items, item)

        if item is None:
            return False

        return super().__contains__(item)


class InventoryManager:
    def __init__(self, record: UserRecord) -> None:
        self.cached: InventoryMapping = InventoryMapping()

        self._record: UserRecord = record
        self._task: asyncio.Task = record.db.loop.create_task(self.fetch_items())

    async def wait(self) -> InventoryManager:
        await self._task
        return self

    async def fetch_items(self) -> None:
        query = 'SELECT * FROM items WHERE user_id = $1'
        records = await self._record.db.fetch(query, self._record.user_id)

        for record in records:
            self.cached[record['item']] = record['count']

    async def add_item(self, item: Item | str, amount: int = 1, *, connection: asyncpg.Connection | None = None) -> None:
        await self.wait()

        query = """
                INSERT INTO items (user_id, item, count) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, item) DO UPDATE SET count = items.count + $3 
                RETURNING items.count
                """

        row = await (connection or self._record.db).fetchrow(query, self._record.user_id, str(item), amount)
        self.cached[item] = row['count']


class Notification(NamedTuple):
    created_at: datetime.datetime
    title: str
    content: str

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Notification:
        return cls(created_at=record['created_at'].replace(tzinfo=datetime.timezone.utc), title=record['title'], content=record['content'])


class NotificationsManager:
    def __init__(self, record: UserRecord) -> None:
        self.cached: list[Notification] | None = None

        self._record: UserRecord = record
        self._task: asyncio.Task = record.db.loop.create_task(self.fetch_notifications())

    async def wait(self) -> NotificationsManager:
        await self._task
        return self

    async def fetch_notifications(self) -> None:
        query = 'SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1000'
        records = await self._record.db.fetch(query, self._record.user_id)

        self.cached = [Notification.from_record(record) for record in records]

    async def add_notification(self, title: str, content: str, *, connection: asyncpg.Connection | None = None) -> None:
        await self.wait()

        query = """
                INSERT INTO notifications (user_id, created_at, title, content)
                VALUES ($1, $2, $3, $4)
                RETURNING *;
                """

        row = await (connection or self._record.db).fetchrow(query, self._record.user_id, datetime.datetime.utcnow(), title, content)
        await self._record.add(unread_notifications=1, connection=connection)
        self.cached.insert(0, Notification.from_record(row))


class UserRecord:
    """Stores data about a user."""

    LEVELING_CURVE = dict(base=100, factor=1.26)

    def __init__(self, user_id: int, *, db: Database) -> None:
        self.db: Database = db
        self.user_id: int = user_id
        self.data: dict[str, Any] = {}

        self.__inventory_manager: InventoryManager | None = None
        self.__notifications_manager: NotificationsManager | None = None

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

    async def _update(self, key: Callable[[tuple[int, str]], str], values: dict[str, Any], *, connection: asyncpg.Connection | None = None) -> UserRecord:
        query = """
                UPDATE users SET {} WHERE user_id = $1
                RETURNING *;
                """

        # noinspection PyTypeChecker
        self.data.update(
            await (connection or self.db).fetchrow(
                query.format(', '.join(map(key, enumerate(values.keys(), start=2)))),
                self.user_id,
                *values.values(),
            ),
        )
        return self

    def update(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Awaitable[UserRecord]:
        return self._update(lambda o: f'"{o[1]}" = ${o[0]}', values, connection=connection)

    def add(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Awaitable[UserRecord]:
        return self._update(lambda o: f'"{o[1]}" = "{o[1]}" + ${o[0]}', values, connection=connection)

    async def add_coins(self, coins: int, /, *, connection: asyncpg.Connection | None = None) -> int:
        """Adds coins including applying multipliers. Returns the amount of coins added."""
        await self.add(wallet=coins, connection=connection)
        return coins

    async def add_exp(self, exp: int, /, *, connection: asyncpg.Connection | None = None) -> bool:
        """Return whether or not the user as leveled up."""
        old = self.level
        await self.add(exp=exp, connection=connection)
        return self.level > old  # TODO: Notify user

    async def add_random_bank_space(self, minimum: int, maximum: int, *, chance: float = 1, connection: asyncpg.Connection | None = None) -> int:
        if random.random() > chance:
            return 0

        await self.add(max_bank=(amount := random.randint(minimum, maximum)), connection=connection)
        return amount

    async def add_random_exp(self, minimum: int, maximum: int, *, chance: float = 1, connection: asyncpg.Connection | None = None) -> int:
        if random.random() > chance:
            return 0

        await self.add_exp(amount := random.randint(minimum, maximum), connection=connection)
        return amount

    async def make_dead(self, *, reason: str | None = None, connection: asyncpg.Connection | None = None) -> None:
        inventory = await self.inventory_manager.wait()
        if inventory.cached.quantity_of('lifesaver'):
            await inventory.add_item('lifesaver', -1, connection=connection)

            return await self.notifications_manager.add_notification(
                title='You almost died!',
                content=f"You almost died{' due to ' + reason if reason else ''}, but you had a lifesaver in your inventory, which is now consumed.",
                connection=connection,
            )

        old = self.wallet
        await self.update(wallet=0, connection=connection)

        available = [(key, value) for key, value in inventory.cached.items() if value]
        if not len(available):
            item, quantity = None, 0
        else:
            item, quantity = random.choice(available)
            await inventory.add_item(item, -quantity, connection=connection)

        await self.notifications_manager.add_notification(
            title='You died!',
            content=(
                f"You died{' due to ' + reason if reason else ''}. "
                f"You lost {Emojis.coin} **{old:,}**{f' and {item.get_sentence_chunk(quantity)}' if item else ''}."
            ),
            connection=connection,
        )

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

    @property
    def unread_notifications(self) -> int:
        return self.data['unread_notifications']

    @property
    def inventory_manager(self) -> InventoryManager:
        if not self.__inventory_manager:
            self.__inventory_manager = InventoryManager(self)

        return self.__inventory_manager

    @property
    def notifications_manager(self) -> NotificationsManager:
        if not self.__notifications_manager:
            self.__notifications_manager = NotificationsManager(self)

        return self.__notifications_manager

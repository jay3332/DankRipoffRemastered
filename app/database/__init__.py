from __future__ import annotations

import asyncio
import datetime
import random
from collections import defaultdict
from dataclasses import dataclass
from string import ascii_letters
from typing import Any, Awaitable, Callable, Generator, Iterable, Literal, NamedTuple, overload, TYPE_CHECKING

import asyncpg
import discord.utils
from discord.utils import cached_property, format_dt

from app.data.items import CropMetadata, Item, Items
from app.data.pets import Pet, Pets
from app.data.skills import Skill, Skills
from app.database.migrations import Migrator
from app.util.common import calculate_level, get_by_key, pick
from config import DatabaseConfig, Emojis, multiplier_guilds

if TYPE_CHECKING:
    from typing import Self

    from app.core import Bot, Command, Context

__all__ = (
    'Database',
    'Migrator',
)


class _Database:
    _internal_pool: asyncpg.Pool

    def __init__(self, *, loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self.__connect_task = self.loop.create_task(self._connect())

    async def wait(self) -> None:
        await self.__connect_task

    async def _connect(self) -> None:
        self._internal_pool = await asyncpg.create_pool(
            host=DatabaseConfig.host,
            port=DatabaseConfig.port,
            user=DatabaseConfig.user,
            database=DatabaseConfig.name,
            password=DatabaseConfig.password,
        )

        async with self.acquire() as conn:
            migrator = Migrator(conn)
            await migrator.run_migrations()

    @overload
    def acquire(self, *, timeout: float = None) -> Awaitable[asyncpg.Connection]:
        ...

    def acquire(self, *, timeout: float = None) -> asyncpg.pool.PoolAcquireContext:
        return self._internal_pool.acquire(timeout=timeout)

    def release(self, conn: asyncpg.Connection, *, timeout: float = None) -> Awaitable[None]:
        return self._internal_pool.release(conn, timeout=timeout)

    def execute(self, query: str, *args: Any, timeout: float = None) -> Awaitable[str]:
        return self._internal_pool.execute(query, *args, timeout=timeout)

    def executemany(self, query: str, args: Iterable[Any], *, timeout: float = None) -> Awaitable[str]:
        return self._internal_pool.executemany(query, args, timeout=timeout)

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

    def __init__(self, bot: Bot, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(loop=loop)
        self.user_records: dict[int, UserRecord] = {}
        self.bot: Bot = bot

        # FIXME: for now, fetch all users and cache them
        #  since there aren't that many records to fetch.
        #  if we ever have to scale, we can remove the following line.
        bot.loop.create_task(self.register_all_records())

    async def register_all_records(self) -> None:
        await self.__connect_task

        query = 'SELECT * FROM users'
        for data in await self.fetch(query):
            user_id = data['user_id']
            self.user_records[user_id] = record = UserRecord(user_id, db=self)
            record.data.update(data)

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
    def get(self, k: Item | str, d: Any = None) -> int:
        return super().get(k, d)

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

    async def _base_update(
        self,
        from_query: str,
        *,
        connection: asyncpg.Connection | None = None,
        transform: Callable[[int, int], int],
        **items: int,
    ) -> None:
        await self.wait()

        await (connection or self._record.db).executemany(
            from_query,
            [(self._record.user_id, k, v) for k, v in items.items()],
        )
        # update is not atomic, so we have to do this
        for k, v in items.items():
            self.cached[k] = transform(self.cached.get(k, 0), v)

    async def update(self, *, connection: asyncpg.Connection | None = None, **items: int) -> None:
        query = """
                INSERT INTO items (user_id, item, count) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, item) DO UPDATE SET count = $3
                """
        await self._base_update(query, connection=connection, transform=lambda _, v: v, **items)

    async def add_bulk(self, *, connection: asyncpg.Connection | None = None, **items: int) -> None:
        query = """
                INSERT INTO items (user_id, item, count) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, item) DO UPDATE SET count = items.count + $3
                """
        await self._base_update(query, connection=connection, transform=lambda p, v: p + v, **items)

    async def wipe(self, *, connection: asyncpg.Connection | None = None) -> None:
        await self.wait()  # this is so the cache isn't prone to data races

        query = 'DELETE FROM items WHERE user_id = $1'
        await (connection or self._record.db).execute(query, self._record.user_id)
        self.cached.clear()


class Notification(NamedTuple):
    created_at: datetime.datetime
    title: str
    content: str

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Notification:
        return cls(created_at=record['created_at'], title=record['title'], content=record['content'])


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

    async def _dispatch_dm_notification(self, title: str, content: str) -> bool:
        bot = self._record.db.bot
        await bot.wait_until_ready()

        try:
            dm_channel = await bot.create_dm(discord.Object(self._record.user_id))
            await dm_channel.send(f'\U0001f514 **{title}**\n{content}')
        except discord.DiscordException:
            return False
        else:
            return True

    async def add_notification(self, title: str, content: str, *, connection: asyncpg.Connection | None = None) -> None:
        await self.wait()

        query = """
                INSERT INTO notifications (user_id, created_at, title, content)
                VALUES ($1, CURRENT_TIMESTAMP, $2, $3)
                RETURNING *;
                """

        args = query, self._record.user_id, title, content

        try:
            row = await (connection or self._record.db).fetchrow(*args)
        except asyncpg.InterfaceError:
            row = await self._record.db.fetchrow(*args)

        self.cached.insert(0, Notification.from_record(row))

        result = False
        if self._record.dm_notifications:
            result = await self._dispatch_dm_notification(title, content)

        if not result:
            await self._record.add(unread_notifications=1, connection=connection)


class SkillInfo(NamedTuple):
    skill: str
    points: int
    cooldown_until: datetime.datetime | None

    def into_skill(self) -> Skill:
        return get_by_key(Skills, self.skill)

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> SkillInfo:
        return cls(skill=record['skill'], points=record['points'], cooldown_until=record['on_cooldown_until'])


class SkillManager:
    def __init__(self, record: UserRecord) -> None:
        self.cached: dict[str, SkillInfo] = {}

        self._record: UserRecord = record
        self._task: asyncio.Task = record.db.loop.create_task(self.fetch_skills())

    async def wait(self) -> SkillManager:
        await self._task
        return self

    async def fetch_skills(self) -> None:
        query = 'SELECT * FROM skills WHERE user_id = $1'
        records = await self._record.db.fetch(query, self._record.user_id)

        self.cached = {record['skill']: SkillInfo.from_record(record) for record in records}

    def get_skill(self, skill: Skill | str) -> SkillInfo | None:
        if not self.has_skill(skill := str(skill)):
            return None

        return self.cached[skill]

    def points_in(self, skill: Skill | str) -> int:
        if skill := self.get_skill(skill):
            return skill.points

        return 0

    def has_skill(self, skill: Skill | str) -> bool:
        return getattr(skill, 'key', skill) in self.cached

    async def add_skill(self, skill: Skill | str, *, connection: asyncpg.Connection | None = None) -> None:
        await self.wait()

        if isinstance(skill, Skill):
            skill = skill.key

        query = """
                INSERT INTO skills (user_id, skill) VALUES ($1, $2)
                ON CONFLICT (user_id, skill) DO UPDATE SET user_id = $1
                RETURNING *;
                """

        row = await (connection or self._record.db).fetchrow(query, self._record.user_id, skill)
        self.cached[skill] = SkillInfo.from_record(row)

    async def add_skill_points(self, skill: Skill | str, points: int, *, connection: asyncpg.Connection | None = None) -> None:
        await self.wait()

        if isinstance(skill, Skill):
            skill = skill.key

        query = """
                INSERT INTO skills (user_id, skill, points) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, skill) DO UPDATE SET points = skills.points + $3
                RETURNING *;
                """

        row = await (connection or self._record.db).fetchrow(query, self._record.user_id, skill, points)
        self.cached[skill] = SkillInfo.from_record(row)

    async def add_skill_cooldown(
        self, skill: Skill | str, cooldown: datetime.timedelta, *, connection: asyncpg.Connection | None = None,
    ) -> None:
        await self.wait()

        if isinstance(skill, Skill):
            skill = skill.key

        query = """
                INSERT INTO skills (user_id, skill, on_cooldown_until) VALUES ($1, $2, CURRENT_TIMESTAMP + $3)
                ON CONFLICT (user_id, skill) DO UPDATE SET on_cooldown_until = CURRENT_TIMESTAMP + $3
                RETURNING *;
                """

        row = await (connection or self._record.db).fetchrow(query, self._record.user_id, skill, cooldown)
        self.cached[skill] = SkillInfo.from_record(row)


class CooldownInfo(NamedTuple):
    command: str
    expires: datetime.datetime
    previous_expiry: datetime.datetime | None

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> CooldownInfo:
        return cls(command=record['command'], expires=record['expires'], previous_expiry=record['previous_expiry'])


class CooldownManager:
    def __init__(self, record: UserRecord) -> None:
        self.cached: dict[str, CooldownInfo] = {}

        self._record: UserRecord = record
        self._task: asyncio.Task = record.db.loop.create_task(self.fetch_cooldowns())

    async def wait(self) -> CooldownManager:
        await self._task
        return self

    def get_cooldown(self, command: Command) -> Literal[False] | float:
        key = command.qualified_name
        if key not in self.cached:
            return False

        difference = (self.cached[key].expires - discord.utils.utcnow()).total_seconds()
        if difference > 0:
            return difference

        return False

    async def fetch_cooldowns(self) -> None:
        query = 'SELECT * FROM cooldowns WHERE user_id = $1 AND CURRENT_TIMESTAMP < expires'
        records = await self._record.db.fetch(query, self._record.user_id)

        self.cached = {
            record['command']: CooldownInfo.from_record(record) for record in records
        }

    async def set_cooldown(self, command: Command, expires: datetime.datetime) -> None:
        await self.wait()

        query = """
                INSERT INTO cooldowns (user_id, command, expires, previous_expiry) VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, command) DO UPDATE SET expires = $3, previous_expiry = $4
                RETURNING *
                """

        key = command.qualified_name
        previous = self.cached[key].expires if key in self.cached else None

        new = await self._record.db.fetchrow(query, self._record.user_id, key, expires, previous)

        self.cached[key] = CooldownInfo.from_record(new)


class CropInfo(NamedTuple):
    x: int
    y: int
    crop: Item[CropMetadata] | None
    exp: int
    last_harvest: datetime.datetime | None
    created_at: datetime.datetime

    @staticmethod
    def get_letters(x: int) -> str:
        letters = ascii_letters[26:52]

        return (' ' + letters)[x // 26].strip() + letters[x % 26]

    @staticmethod
    def into_coordinates(x: int, y: int) -> str:
        return CropInfo.get_letters(x) + str(y + 1)

    @cached_property
    def coordinates(self) -> str:
        return self.into_coordinates(self.x, self.y)

    @property
    def level_data(self) -> tuple[int, int, int]:
        return calculate_level(self.exp, **CropManager.LEVELING_CURVE)

    @property
    def level(self) -> int:
        return self.level_data[0]

    @property
    def xp(self) -> int:
        return self.level_data[1]

    @property
    def max_xp(self) -> int:
        return self.level_data[2]

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> CropInfo:
        return cls(
            x=record['x'],
            y=record['y'],
            crop=get_by_key(Items, record['crop']),
            exp=record['exp'],
            last_harvest=record['last_harvest'],
            created_at=record['created_at'],
        )


class CropManager:
    LEVELING_CURVE = dict(base=50, factor=1.15)

    def __init__(self, record: UserRecord) -> None:
        self.cached: dict[tuple[int, int], CropInfo] = {}

        self._record: UserRecord = record
        self._task: asyncio.Task = record.db.loop.create_task(self.fetch_crops())

    async def wait(self) -> CropManager:
        await self._task
        return self

    async def fetch_crops(self) -> None:
        query = 'SELECT * FROM crops WHERE user_id = $1'

        async with self._record.db.acquire() as conn:
            records = await conn.fetch(query, self._record.user_id)
            self.cached = {
                (record['x'], record['y']): CropInfo.from_record(record) for record in records
            }

            default = [
                (self._record.user_id, x, y) for x in range(4) for y in range(4)
                if (x, y) not in self.cached
            ]
            if not default:
                return

            await conn.executemany('INSERT INTO crops (user_id, x, y) VALUES ($1, $2, $3)', default)

            records = await conn.fetch(query, self._record.user_id)
            self.cached = {
                (record['x'], record['y']): CropInfo.from_record(record) for record in records
            }

    def get_crop_info(self, x: int, y: int) -> CropInfo:
        return self.cached.get((x, y))

    async def harvest(self, coordinates: list[tuple[int, int]]) -> tuple[dict[tuple[int, int], tuple[Item, int]], dict[Item, int]]:
        level_ups = {}
        harvested = defaultdict(int)

        await self.wait()

        async with self._record.db.acquire() as conn:
            for x, y in coordinates:
                info = self.get_crop_info(x, y)
                if info is None or info.crop is None or (
                    info.last_harvest + datetime.timedelta(seconds=info.crop.metadata.time) > discord.utils.utcnow()
                ):
                    continue

                old_level = info.level
                query = """
                        UPDATE crops SET last_harvest = CURRENT_TIMESTAMP, exp = exp + $4
                        WHERE user_id = $1 AND x = $2 AND y = $3
                        RETURNING *;
                        """
                new = await conn.fetchrow(query, self._record.user_id, x, y, random.randint(5, 10))
                self.cached[x, y] = new = CropInfo.from_record(new)

                if new.level > old_level:
                    level_ups[x, y] = info.crop, new.level

                harvested[info.crop.metadata.item] += random.randint(*info.crop.metadata.count)

            for item, quantity in harvested.items():
                await self._record.inventory_manager.add_item(item, quantity, connection=conn)

        return level_ups, harvested

    async def add_crop_exp(self, x: int, y: int, exp: int) -> bool:
        await self.wait()

        query = """
                UPDATE crops SET exp = exp + $4
                WHERE user_id = $1 AND x = $2 AND y = $3
                RETURNING *;
                """

        old = self.cached[x, y].level

        new = await self._record.db.fetchrow(query, self._record.user_id, x, y, exp)
        self.cached[x, y] = new = CropInfo.from_record(new)

        return new.level > old

    async def update_last_harvest(self, x: int, y: int) -> None:
        await self.wait()

        query = """
                UPDATE crops SET last_harvest = CURRENT_TIMESTAMP
                WHERE user_id = $1 AND x = $2 AND y = $3
                RETURNING *;
                """

        new = await self._record.db.fetchrow(query, self._record.user_id, x, y)
        self.cached[x, y] = CropInfo.from_record(new)

    async def plant_crop(self, coordinates: Iterable[tuple[int, int]], crop: Item | str) -> None:
        if isinstance(crop, Item):
            crop = crop.key

        await self.wait()

        query = """
                UPDATE crops SET crop = $2, last_harvest = CURRENT_TIMESTAMP, exp = 0
                WHERE user_id = $1 AND x = $3 AND y = $4
                RETURNING *;
                """
        for x, y in coordinates:
            new = await self._record.db.fetchrow(query, self._record.user_id, crop, x, y)
            self.cached[x, y] = CropInfo.from_record(new)

    async def add_land(self, x: int, y: int) -> None:
        await self.wait()

        query = """
                INSERT INTO crops (user_id, x, y) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, x, y) DO UPDATE SET user_id = $1
                RETURNING *;
                """

        new = await self._record.db.fetchrow(query, self._record.user_id, x, y)
        self.cached[x, y] = CropInfo.from_record(new)

    async def remove_land(self, x: int, y: int) -> None:
        await self.wait()

        query = """
                DELETE FROM crops
                WHERE user_id = $1 AND x = $2 AND y = $3;
                """

        await self._record.db.execute(query, self._record.user_id, x, y)
        self.cached.pop((x, y), None)

    async def wipe_keeping_land(self, connection: asyncpg.Connection | None = None) -> None:
        await self.wait()

        query = """
                UPDATE crops SET crop = NULL, exp = 0, last_harvest = NULL
                WHERE user_id = $1;
                """

        await (connection or self._record.db).execute(query, self._record.user_id)
        for k in self.cached:
            self.cached[k] = self.cached[k]._replace(crop=None, exp=0, last_harvest=None)


@dataclass
class PetRecord:
    manager: PetManager
    pet: Pet
    total_exp: int
    duplicates: int
    evolution: int
    last_recorded_energy: int
    last_feed: datetime.datetime
    max_energy: int
    equipped: bool

    @property
    def level_data(self) -> tuple[int, int, int]:
        base, factor = self.pet.leveling_curve
        return calculate_level(self.total_exp, base=base, factor=factor, precision=10)

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
    def is_max_level(self) -> int:
        return self.level >= self.pet.max_level

    @property
    def energy(self) -> int:
        if self.last_recorded_energy <= 0:
            return 0
        elapsed = discord.utils.utcnow() - self.last_feed
        return max(0, round(self.last_recorded_energy - elapsed.total_seconds() / 60 * self.pet.energy_per_minute))

    @property
    def exhausts_at(self) -> datetime.datetime:
        return self.last_feed + datetime.timedelta(minutes=self.last_recorded_energy / self.pet.energy_per_minute)

    @staticmethod
    def _transform_record(record: asyncpg.Record) -> dict[str, Any]:
        return pick(
            record,
            'duplicates', 'evolution', 'last_recorded_energy', 'last_feed', 'max_energy', 'equipped',
            exp='total_exp',
        )

    @classmethod
    def from_record(cls, manager: PetManager, record: asyncpg.Record) -> Self:
        return cls(manager=manager, pet=get_by_key(Pets, record['pet']), **cls._transform_record(record))

    @property
    def user_id(self) -> int:
        return self.manager._record.user_id

    @property
    def db(self) -> Database:
        return self.manager._record.db

    async def update_with(self, query: str, *, connection: asyncpg.Connection | None = None, **kwargs: Any) -> None:
        record = await (self.db or connection).fetchrow(query, self.user_id, self.pet.key, *kwargs.values())
        self.__dict__.update(**self._transform_record(record))

    async def update(self, *, connection: asyncpg.Connection | None = None, **kwargs: Any) -> None:
        query = 'UPDATE pets SET {0} WHERE user_id = $1 AND pet = $2 RETURNING *'.format(
            ', '.join(f'{k} = ${i}' for i, k in enumerate(kwargs, start=3))
        )
        await self.update_with(query, connection=connection, **kwargs)

    async def add(self, *, connection: asyncpg.Connection | None = None, **kwargs: Any) -> None:
        query = 'UPDATE pets SET {0} WHERE user_id = $1 AND pet = $2 RETURNING *'.format(
            ', '.join(f'{k} = {k} + ${i}' for i, k in enumerate(kwargs, start=3))
        )
        await self.update_with(query, connection=connection, **kwargs)

    async def set_energy(self, energy: int, *, connection: asyncpg.Connection | None = None) -> None:
        query = """
                UPDATE pets SET last_recorded_energy = $2, last_feed = CURRENT_TIMESTAMP
                WHERE user_id = $1
                RETURNING last_feed;
                """

        self.last_feed = await (connection or self.db).fetchval(query, self.user_id, energy)
        self.last_recorded_energy = energy

    async def add_energy(self, energy: int, *, connection: asyncpg.Connection | None = None) -> None:
        energy = max(0, min(self.max_energy, self.energy + energy))
        await self.set_energy(energy, connection=connection)

    async def evolve(self) -> None:
        async with self.db.acquire() as conn:
            await self.add(evolution=1, connection=conn)
            await self.update(exp=0, connection=conn)
            await self.set_energy(0, connection=conn)


class PetManager:
    def __init__(self, record: UserRecord) -> None:
        self._record = record
        self.cached: dict[Pet, PetRecord] = {}
        self.__fetch_task = asyncio.create_task(self.fetch())

    async def wait(self) -> Self:
        await self.__fetch_task
        return self

    async def fetch(self) -> None:
        await self._record.db.wait()
        query = 'SELECT * FROM pets WHERE user_id = $1;'
        records = await self._record.db.fetch(query, self._record.user_id)
        records = (PetRecord.from_record(manager=self, record=r) for r in records)
        self.cached = {r.pet: r for r in records}

    def get_active_pet(self, pet: Pet) -> PetRecord | None:
        if record := self.cached.get(pet):
            return record if record.equipped and record.energy > 0 else None

    @property
    def equipped_count(self) -> int:
        return sum(r.equipped for r in self.cached.values())

    async def add_pet(self, pet: Pet, *, connection: asyncpg.Connection | None = None) -> None:
        query = 'INSERT INTO pets (user_id, pet, max_energy) VALUES ($1, $2, $3) RETURNING *'
        record = await (connection or self._record.db).fetchrow(
            query, self._record.user_id, pet.key, pet.max_energy,
        )
        self.cached[pet] = PetRecord.from_record(manager=self, record=record)


class UserHistoryEntry(NamedTuple):
    wallet: int
    total: int

    @property
    def bank(self) -> int:
        return self.total - self.wallet

    @classmethod
    def from_record(cls, record: asyncpg.Record) -> Self:
        return cls(record['wallet'], record['total'])


class Multiplier(NamedTuple):
    multiplier: float
    title: str
    description: str | None = None
    expires_at: datetime.datetime | None = None
    is_global: bool = True

    @property
    def display(self) -> str:
        base = f'- {self.title}: +**{self.multiplier:.1%}** {"(global)" if self.is_global else ""}'

        if description := self.description:
            base += f'\n  - *{description}*'

        if expires_at := self.expires_at:
            base += f'\n  - Expires {format_dt(expires_at, "R")}'

        return base


class UserRecord:
    """Stores data about a user."""

    LEVELING_CURVE = dict(base=100, factor=1.22)
    ALCOHOL_ACTIVE_DURATION = datetime.timedelta(hours=2)

    def __init__(self, user_id: int, *, db: Database) -> None:
        self.db: Database = db
        self.user_id: int = user_id
        self.data: dict[str, Any] = {}

        self.history: list[tuple[datetime.datetime, UserHistoryEntry]] = []  # Experimental
        self.__history_fetched: bool = False

        self.__inventory_manager: InventoryManager | None = None
        self.__notifications_manager: NotificationsManager | None = None
        self.__cooldown_manager: CooldownManager | None = None
        self.__skill_manager: SkillManager | None = None
        self.__crop_manager: CropManager | None = None
        self.__pet_manager: PetManager | None = None

    def __repr__(self) -> str:
        return f'<UserRecord wallet={self.wallet} bank={self.bank} level_data={self.level_data}>'

    async def update_history(self, connection: asyncpg.Connection) -> None:
        if self.history:
            _, previous = self.history[-1]
            # Prevent a useless duplicate entry
            if previous.wallet == self.wallet and previous.total == self.total_coins:
                return

        query = 'INSERT INTO user_coins_graph_data (user_id, wallet, total) VALUES ($1, $2, $3) RETURNING *;'
        record = await connection.fetchrow(query, self.user_id, self.wallet, self.total_coins)
        self.history.append((record['timestamp'], UserHistoryEntry.from_record(record)))

    async def fetch(self) -> UserRecord:
        await self.db.wait()
        query = """
                INSERT INTO users (user_id) VALUES ($1) 
                ON CONFLICT (user_id) DO UPDATE SET user_id = $1 -- useless upsert
                RETURNING *;
                """

        async with self.db.acquire() as conn:
            self.data.update(await conn.fetchrow(query, self.user_id))  # TODO: Welcome user if new
            await self.fetch_history(connection=conn)

        return self

    async def fetch_history(self, connection: asyncpg.Connection) -> None:
        self.__history_fetched = True
        self.history = [
            (record['timestamp'], UserHistoryEntry.from_record(record))
            for record in await connection.fetch(
                'SELECT * FROM user_coins_graph_data WHERE user_id = $1 ORDER BY timestamp',
                self.user_id,
            )
        ]
        if not self.history:
            await self.update_history(connection=connection)

    async def fetch_if_necessary(self) -> UserRecord:
        if not len(self.data):
            await self.fetch()

        if not self.__history_fetched:
            async with self.db.acquire() as conn:
                await self.fetch_history(connection=conn)

        return self

    async def _update(
        self,
        key: Callable[[tuple[int, str]], str],
        values: dict[str, Any],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> UserRecord:
        query = "/**/ UPDATE users SET {} WHERE user_id = $1 RETURNING *;"  # prevent language injection with /**/
        actual_conn = await self.db.acquire() if connection is None else connection

        # noinspection PyTypeChecker
        try:
            self.data.update(
                await actual_conn.fetchrow(
                    query.format(', '.join(map(key, enumerate(values.keys(), start=2)))),
                    self.user_id,
                    *values.values(),
                ),
            )
            await self.update_history(connection=actual_conn)
        finally:
            if connection is None:
                await self.db.release(actual_conn)
        return self

    def update(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Awaitable[UserRecord]:
        return self._update(lambda o: f'"{o[1]}" = ${o[0]}', values, connection=connection)

    def add(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Awaitable[UserRecord]:
        return self._update(lambda o: f'"{o[1]}" = "{o[1]}" + ${o[0]}', values, connection=connection)

    def append(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Awaitable[UserRecord]:
        return self._update(lambda o: f'"{o[1]}" = ARRAY_APPEND("{o[1]}", ${o[0]})', values, connection=connection)

    async def add_coins(self, coins: int, /, *, connection: asyncpg.Connection | None = None) -> int:
        """Adds coins including applying multipliers. Returns the amount of coins added."""
        coins = round(coins * self.coin_multiplier)

        await self.add(wallet=coins, connection=connection)
        return coins

    async def add_exp(
        self, exp: int, /, *, ctx: Context | None = None, connection: asyncpg.Connection | None = None,
    ) -> bool:
        """Return whether the user has leveled up."""
        old = self.level
        multiplier = self.exp_multiplier_in_ctx(ctx)
        exp = round(exp * multiplier)
        await self.add(exp=exp, connection=connection)

        if self.level > old:
            await self.notifications_manager.add_notification(
                title='You leveled up!',
                content=f'Congratulations on leveling up to **Level {self.level}**.',
                connection=connection,
            )
            return True

        return False

    async def add_random_bank_space(self, minimum: int, maximum: int, *, chance: float = 1, connection: asyncpg.Connection | None = None) -> int:
        if random.random() > chance:
            return 0

        amount = round(random.randint(minimum, maximum) * self.bank_space_growth_multiplier)
        await self.add(max_bank=amount, connection=connection)
        return amount

    async def add_random_exp(
        self, minimum: int, maximum: int, *, chance: float = 1,
        ctx: Context | None = None, connection: asyncpg.Connection | None = None,
    ) -> int:
        if random.random() > chance:
            return 0

        amount = random.randint(minimum, maximum)
        await self.add_exp(amount, ctx=ctx, connection=connection)
        return amount

    async def make_dead(self, *, reason: str | None = None, connection: asyncpg.Connection | None = None) -> None:
        inventory = await self.inventory_manager.wait()
        if inventory.cached.quantity_of('lifesaver'):
            await inventory.add_item('lifesaver', -1, connection=connection)

            await self.notifications_manager.add_notification(
                title='You almost died!',
                content=f"You almost died{' due to ' + reason if reason else ''}, but you had a lifesaver in your inventory, which is now consumed.",
                connection=connection,
            )
            return

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
        return self.max_bank and self.bank / self.max_bank

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
    def base_exp_multiplier(self) -> float:
        return self.data['exp_multiplier']

    def walk_exp_multipliers(self, ctx: Context | None = None) -> Generator[Multiplier, Any, Any]:
        yield Multiplier(
            self.base_exp_multiplier,
            'Base Multiplier',
            description='accumulated from using items like cheese',
        )
        yield Multiplier(self.prestige * 0.25, f'{Emojis.get_prestige_emoji(self.prestige)} Prestige {self.prestige}')

        if ctx is not None and ctx.guild.id in multiplier_guilds:
            yield Multiplier(0.5, ctx.guild.name, is_global=False)

    @property
    def global_exp_multiplier(self) -> float:
        return self.exp_multiplier_in_ctx(None)

    def exp_multiplier_in_ctx(self, ctx: Context | None = None) -> float:
        return 1 + sum(m.multiplier for m in self.walk_exp_multipliers(ctx))

    def walk_coin_multipliers(self, _ctx: Context | None = None) -> Generator[Multiplier, Any, Any]:
        yield Multiplier(self.prestige * 0.25, f'{Emojis.get_prestige_emoji(self.prestige)} Prestige {self.prestige}')
        yield Multiplier(
            (self.alcohol_expiry is not None) * 0.25,
            f'{Items.alcohol.emoji} Alcohol',
            expires_at=self.alcohol_expiry,
        )

    @property
    def coin_multiplier(self) -> float:
        return 1 + sum(m.multiplier for m in self.walk_coin_multipliers())

    def walk_bank_space_growth_multipliers(self) -> Generator[Multiplier, Any, Any]:
        yield Multiplier(self.prestige * 0.5, f'{Emojis.get_prestige_emoji(self.prestige)} Prestige {self.prestige}')

    @property
    def bank_space_growth_multiplier(self) -> float:
        return 1 + sum(m.multiplier for m in self.walk_bank_space_growth_multipliers())

    @property
    def prestige(self) -> int:
        return self.data['prestige']

    @property
    def padlock_active(self) -> bool:
        return self.data['padlock_active']

    @property
    def last_alcohol_usage(self) -> datetime.datetime | None:
        return self.data.get('last_alcohol_usage')

    @property
    def alcohol_expiry(self) -> datetime.datetime | None:
        if self.last_alcohol_usage is None:
            return None

        elapsed = discord.utils.utcnow() - self.last_alcohol_usage
        if elapsed > self.ALCOHOL_ACTIVE_DURATION:
            return None
        return self.last_alcohol_usage + self.ALCOHOL_ACTIVE_DURATION

    @property
    def unread_notifications(self) -> int:
        return self.data['unread_notifications']

    @property
    def daily_streak(self) -> int:
        return self.data['daily_streak']

    @property
    def weekly_streak(self) -> int:
        return self.data['weekly_streak']

    @property
    def discovered_recipes(self) -> list[str]:
        return self.data['discovered_recipes']

    @property
    def dm_notifications(self) -> bool:
        return self.data['dm_notifications']

    @property
    def max_equipped_pets(self) -> int:
        return self.data['max_equipped_pets']

    @property
    def pet_operations(self) -> int:
        return self.data['pet_operations']

    @property
    def pet_operations_cooldown_start(self) -> datetime.datetime:
        return self.data['pet_operations_cooldown_start']

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

    @property
    def cooldown_manager(self) -> CooldownManager:
        if not self.__cooldown_manager:
            self.__cooldown_manager = CooldownManager(self)

        return self.__cooldown_manager

    @property
    def skill_manager(self) -> SkillManager:
        if not self.__skill_manager:
            self.__skill_manager = SkillManager(self)

        return self.__skill_manager

    @property
    def crop_manager(self) -> CropManager:
        if not self.__crop_manager:
            self.__crop_manager = CropManager(self)

        return self.__crop_manager

    @property
    def pet_manager(self) -> PetManager:
        if not self.__pet_manager:
            self.__pet_manager = PetManager(self)

        return self.__pet_manager

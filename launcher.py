import asyncio
from sys import argv

import asyncpg

from app.core.bot import Bot
from app.database.migrations import Migrator
from config import DatabaseConfig


async def run_migrations() -> None:
    conn = await asyncpg.connect(
        host=DatabaseConfig.host,
        port=DatabaseConfig.port,
        user=DatabaseConfig.user,
        database=DatabaseConfig.name,
        password=DatabaseConfig.password,
    )
    await Migrator(conn).run_migrations(debug=True)


if __name__ == '__main__':
    match argv:
        case [_, 'migrate' | 'm' | 'migration' | 'migrations', *args]:
            match args:
                case ['add' | 'new' | 'create' | '+', name]:
                    Migrator.create_migration(name)
                case ['run' | 'r' | 'execute' | 'exec']:
                    asyncio.run(run_migrations())
                case _:
                    raise RuntimeError('Invalid command.')
        case _:
            Bot().run()

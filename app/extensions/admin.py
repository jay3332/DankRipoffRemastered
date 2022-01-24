from __future__ import annotations

import contextlib
from asyncio import subprocess
from io import StringIO
from typing import Any, TYPE_CHECKING, Type, TypeAlias

import tabulate
from jishaku.codeblocks import codeblock_converter

from app.core import Cog, Context, REPLY, group
from app.database import Migrator
from app.util.common import humanize_small_duration, pluralize
from app.util.structures import Timer

if TYPE_CHECKING:
    from app.core import Bot
    from jishaku.codeblocks import Codeblock

    codeblock_converter: TypeAlias = Type[Codeblock]


class Admin(Cog):
    """Administrator/owner-only commands."""

    async def cog_check(self, ctx: Context) -> bool:
        return await ctx.bot.is_owner(ctx.author)

    @group(aliases={'db'})
    async def database(self, ctx: Context):
        """Commands that wrap around the database."""
        await ctx.send_help(ctx.command)

    @database.command(aliases={'run', 'query', 'fetch', 'q'})
    async def sql(self, ctx: Context, *, sql: codeblock_converter) -> tuple[str, Any]:
        """Fetches the results of a SQL query."""
        async with ctx.typing():
            with Timer() as timer:
                try:
                    result = await ctx.db.fetch(sql.content)
                except Exception as exc:
                    return f"Error!\n```sql\n{exc}```", REPLY

            time = f'in {humanize_small_duration(timer.time)}: '

            if not result:
                return f"{time} No results.", REPLY

            table_raw = tabulate.tabulate(result, headers="keys", tablefmt="fancy_grid")
            time = pluralize(f'{len(result):,} result(s) in {time}')

            message = f"{time}\n```sql\n{table_raw}```"

            if len(message) <= 2000 and all(len(line) < 140 for line in message.split('\n')):
                return message, REPLY

            return f'{time}\nToo many rows or columns to display. Consider narrowing down your query.', REPLY

    @database.group(aliases={'mig', 'm', 'migrate', 'migration'})
    async def migrations(self, ctx: Context):
        """Manages database migrations."""
        await ctx.send_help(ctx.command)

    @migrations.command('add', aliases={'+', 'new', 'create'})
    async def db_migrations_add(self, ctx: Context, name: str, *, sql: codeblock_converter) -> str:
        """Creates a new migration."""
        out = StringIO()

        async with ctx.typing():
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(out),
            ):
                filename = Migrator.create_migration(name)

                with open(filename, 'w') as fp:
                    fp.write(sql.content)

                await subprocess.create_subprocess_shell(f'git add {filename}')

            out.seek(0)

        ctx.bot.loop.create_task(ctx.thumbs())
        return f'```{out.read()}```'

    @migrations.command('run', aliases={'execute', 'exec', 'r', 'push'})
    async def db_migrations_run(self, ctx: Context) -> str:
        """Runs pending migrations."""
        out = StringIO()

        async with ctx.typing():
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(out),
            ):
                async with ctx.db.acquire() as conn:
                    migrator = Migrator(conn)
                    await migrator.run_migrations(debug=True)

            out.seek(0)

        ctx.bot.loop.create_task(ctx.thumbs())
        return f'```{out.read()}```'


def setup(bot: Bot) -> None:
    bot.add_cog(Admin(bot))

from __future__ import annotations

import contextlib
from asyncio import subprocess
from io import StringIO
from typing import Any, TYPE_CHECKING

from jishaku.codeblocks import codeblock_converter

from app.core import Cog, Context, EDIT, command, group, simple_cooldown
from app.database import Migrator
from app.util.structures import Timer

if TYPE_CHECKING:
    from app.core import Bot


class Admin(Cog):
    """Administrator/owner-only commands."""

    async def cog_check(self, ctx: Context) -> bool:
        return await ctx.bot.is_owner(ctx.author)

    @group(aliases={'db'})
    async def database(self, ctx: Context):
        """Commands that wrap around the database."""
        await ctx.send_help(ctx.command)

    @database.group(aliases={'mig', 'm', 'migrate', 'migration'})
    async def migrations(self, ctx: Context):
        """Manages database migrations."""
        await ctx.send_help(ctx.command)

    @migrations.command(aliases={'+', 'new', 'create'})
    async def add(self, ctx: Context, name: str, *, sql: codeblock_converter) -> str:
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

    @migrations.command(aliases={'execute', 'exec', 'r', 'push'})
    async def run(self, ctx: Context) -> str:
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

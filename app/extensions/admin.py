from __future__ import annotations

import contextlib
import importlib
from asyncio import subprocess, create_subprocess_exec
from asyncio.subprocess import PIPE
from io import StringIO
from typing import Any, NamedTuple, TYPE_CHECKING, Type, TypeAlias

import discord
import tabulate
from jishaku.codeblocks import codeblock_converter

from app.core import Cog, Context, REPLY, group
from app.database import Migrator
from app.util.ansi import AnsiStringBuilder, AnsiColor
from app.util.common import humanize_small_duration, pluralize
from app.util.converters import BYPASS, ItemAndQuantityConverter, RichInteger
from app.util.structures import Timer
from app.util.views import UserView
from config import Colors, Emojis

if TYPE_CHECKING:
    from jishaku.codeblocks import Codeblock

    from app.core import Bot
    from app.util.types import CommandResponse, TypedInteraction

    codeblock_converter: TypeAlias = Type[Codeblock]


class GitPullOutput(NamedTuple):
    modified: list[str]
    summary: str


class GitPullView(UserView):
    def __init__(self, ctx: Context, output: GitPullOutput):
        super().__init__(ctx.author)
        self.ctx: Context = ctx
        self.bot: Bot = ctx.bot
        self.output: GitPullOutput = output

        if not self.output.modified:
            self.reload_modified.disabled = True

    @discord.ui.button(label='Reload Modified Files', style=discord.ButtonStyle.primary)
    async def reload_modified(self, interaction: TypedInteraction, _) -> None:
        response = AnsiStringBuilder()

        async with self.ctx.typing():
            for file in self.output.modified:
                if not file.endswith('.py'):
                    continue

                module = file.replace('/', '.').replace('.py', '')  # Super unreliable but it gets the job done

                if module.startswith('app.extensions'):
                    try:
                        if module in self.bot.extensions:
                            await self.bot.reload_extension(module)
                            color, extra = AnsiColor.green, 'reloaded'
                        else:
                            await self.bot.load_extension(module)
                            color, extra = AnsiColor.cyan, 'loaded'

                    except Exception as exc:
                        color, extra = AnsiColor.red, str(exc)

                    response.append(module + ' ', color=color, bold=True)
                    response.append(extra, color=AnsiColor.gray).newline()
                    continue

                try:
                    resolved = importlib.import_module(module)
                    importlib.reload(resolved)
                except Exception as exc:
                    response.append(module + ' ', color=AnsiColor.red, bold=True)
                    response.append(str(exc), color=AnsiColor.gray).newline()
                else:
                    response.append(module + ' ', color=AnsiColor.green, bold=True)
                    response.append('reloaded non-extension', color=AnsiColor.gray).newline()

        await interaction.response.send_message(response.ensure_codeblock().dynamic(self.ctx))

    @discord.ui.button(label='Restart Bot', style=discord.ButtonStyle.danger)
    async def restart_bot(self, interaction: TypedInteraction, _) -> None:
        await interaction.response.send_message('Restarting...')
        await self.bot.close()


class Admin(Cog):
    """Administrator/owner-only commands."""

    __hidden__ = True
    emoji = f'\U0001f6e0\ufe0f'

    async def cog_check(self, ctx: Context) -> bool:
        return await ctx.bot.is_owner(ctx.author)

    @group(aliases={'db'})
    async def database(self, ctx: Context):
        """Commands that wrap around the database."""
        await ctx.send_help(ctx.command)

    @database.command(aliases={'run', 'query', 'fetch', 'q'})
    async def sql(self, ctx: Context, *, sql: codeblock_converter) -> Any:
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

            # noinspection PyTypeChecker
            file = discord.File(StringIO(table_raw), filename='response.txt')
            return time, file, REPLY

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

    @group(aliases={'g', 'github', 'remote'})
    async def git(self, ctx: Context) -> None:
        """Manages requests between the bot it's Git remote."""
        await ctx.send_help(ctx.command)

    @staticmethod
    def _parse_git_output(output: str) -> GitPullOutput:
        idx = output.rfind('Fast-forward')
        if idx == -1:
            return GitPullOutput([], 'No files changed.')

        *modified, summary = output[idx + 13:].splitlines()
        modified = [f.rsplit(' | ', maxsplit=1)[0].strip() for f in modified]

        return GitPullOutput(modified, summary.strip())

    @git.command(aliases={'update'})
    async def pull(self, ctx: Context) -> CommandResponse:
        """Updates the local repository with changes from the remote repository."""
        async with ctx.typing():
            proc = await create_subprocess_exec("git", "pull", stdout=PIPE, stderr=PIPE)
            raw = '-'

            stdout, stderr = await proc.communicate()
            try:
                stdout, stderr = stdout.decode(), stderr.decode()
            except UnicodeDecodeError:
                output = GitPullOutput([], 'Failed to decode output.')
            else:
                output = self._parse_git_output(stdout)
                raw = f'```ansi\n{stdout}\n\n{stderr}```'

                if len(raw) > 2000:
                    raw = 'Output too long to display.'

        if not output.modified:
            color = Colors.warning if output.summary.startswith('N') else Colors.error
        else:
            color = Colors.success

        embed = discord.Embed(color=color, description=raw, timestamp=ctx.now)
        embed.add_field(name='Summary', value=output.summary, inline=False)

        modified = '\n'.join(output.modified[:16])
        if len(output.modified) > 16:
            modified += f'\n*{len(output.modified) - 16} more...*'

        embed.add_field(name='Modified Files', value=modified if output.modified else 'None')

        return embed, GitPullView(ctx, output), REPLY

    @group('dev', aliases={'developer', 'admin', 'adm', 'sudo'})
    async def developer(self, ctx: Context) -> None:
        """Developer-only commands."""
        await ctx.send_help(ctx.command)

    @developer.command('spawn', aliases={'add', '+', 'give'})
    async def dev_spawn(
        self,
        ctx: Context,
        user: discord.User | None = None,
        *,
        entity: RichInteger | ItemAndQuantityConverter(BYPASS),
    ) -> CommandResponse:
        """Spawn a few coins or items for a user."""
        user = user or ctx.author
        record = await ctx.db.get_user_record(user.id)

        if isinstance(entity, int):
            verb = 'Spawned' if entity >= 0 else 'Removed'
            await record.add(wallet=entity)
            return f'{verb} {Emojis.coin} **{abs(entity):,}** in {user.mention}\'s wallet.', REPLY

        item, quantity = entity
        verb = 'Spawned' if quantity >= 0 else 'Removed'
        await record.inventory_manager.add_item(item, quantity)
        return f'{verb} {item.get_sentence_chunk(abs(quantity))} in {user.mention}\'s inventory.', REPLY


setup = Admin.simple_setup

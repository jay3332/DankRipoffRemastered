from __future__ import annotations

import asyncio
import datetime
import functools
import random
import re
from collections import defaultdict
from typing import Any, NamedTuple, TYPE_CHECKING

import discord
from aiohttp import ClientTimeout
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.utils import format_dt, oauth_url

from app.core import BAD_ARGUMENT, Bot, Cog, Context, EDIT, REPLY, command, group, simple_cooldown
from app.core.timers import Timer
from app.data.items import Items
from app.data.settings import Setting, Settings
from app.extensions.events import VOTE_REWARDS
from app.util.common import converter, cutoff, format_line, pluralize, walk_collection
from app.util.converters import better_bool, query_setting
from app.util.pagination import FieldBasedFormatter, LineBasedFormatter, Paginator
from app.util.structures import Timer as PingTimer
from app.util.types import CommandResponse, TypedInteraction
from app.util.views import UserView
from config import Colors, Emojis, default_permissions, support_server, website

if TYPE_CHECKING:
    from typing import Self

    from app.core import Command, HybridContext


@converter
async def CommandConverter(ctx: Context, argument: str) -> Command:
    """Converts a command name into a Command object."""
    if cmd := ctx.bot.get_command(argument):
        return cmd
    raise commands.BadArgument(f'Command "{argument}" not found.')


async def _get_retry_after(ctx: Context, cmd: Command) -> float:
    if getattr(cmd.callback, '__database_cooldown__', None):
        record = await ctx.db.get_user_record(ctx.author.id)
        cooldowns = await record.cooldown_manager.wait()
        return cooldowns.get_cooldown(cmd)

    if bucket := cmd._buckets.get_bucket(ctx):
        return bucket.get_retry_after(discord.utils.utcnow().timestamp())
    return 0.0


class CooldownReminderMetadata(NamedTuple):
    channel_id: int
    jump_url: str | None
    timer_id: int

    @classmethod
    def from_timer(cls: type[Self], timer: Timer) -> Self:
        metadata = timer.metadata
        return cls(channel_id=metadata['channel_id'], jump_url=metadata['jump_url'], timer_id=timer.id)


class Guide:
    BACKSLASH_SUBSTITUTION = re.compile(r'\\.', re.MULTILINE)

    @classmethod
    def walk_markdown(cls, ctx: Context, lines: list[str], embed: discord.Embed) -> None:
        index = 0
        current = []
        current_field = None

        while index < len(lines):
            line = lines[index]
            line = line.strip()
            line = format_line(ctx, line)
            while line.endswith('\\'):
                index += 1
                line = line[:-1] + format_line(ctx, lines[index].strip())

            if line.startswith('##'):
                if current:
                    if current_field is not None:
                        embed.add_field(name=current_field, value='\n'.join(current), inline=False)
                    elif current:
                        embed.description = '\n'.join(current)
                    current = []

                current_field = line[2:].lstrip()
            elif line.startswith('#'):
                embed.title = line[1:].lstrip()
            else:
                current.append(line)

            index += 1

        if current_field is not None:
            embed.add_field(name=current_field, value='\n'.join(current), inline=False)
        elif current:
            embed.description = '\n'.join(current)

    @classmethod
    def view(cls, ctx: Context) -> discord.ui.View:
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label='Invite Coined to your server!',
                url=oauth_url(ctx.bot.user.id, permissions=discord.Permissions(default_permissions)),
            ),
        )
        view.add_item(
            discord.ui.Button(label='Support Server', url=support_server),
        )
        view.add_item(
            discord.ui.Button(label='Website', url=website),
        )
        return view

    @classmethod
    def render(cls, ctx: Context, lines: list[str]) -> discord.Embed:
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_thumbnail(url=ctx.bot.user.avatar)
        embed.set_author(name='Coined Guide', icon_url=ctx.author.display_avatar)

        cls.walk_markdown(ctx, lines, embed)
        return embed


class Miscellaneous(Cog):
    """Miscellaneous commands."""

    emoji = '<:thumbs_up:1131835741358530640>'

    PONG_MESSAGES = (
        'Pong.',
        'Pong!',
        'Pong?',
        'Pong!?',
    )

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self._guides: dict[str, list[str]] = {}
        self._cooldown_reminder_exists = defaultdict[int, dict[str, CooldownReminderMetadata]](dict)
        self.__cooldown_reminder_fetch_task = self.bot.loop.create_task(self._fetch_cooldown_reminders())

    async def render_guide(self, ctx: Context, page: str) -> discord.Embed:
        if page not in self._guides:
            with open(f'./guide/{page}.md', 'r') as f:
                self._guides[page] = lines = f.readlines()

            embed = Guide.render(ctx, lines)
            return embed

        embed = Guide.render(ctx, self._guides[page])
        return embed

    async def _fetch_cooldown_reminders(self) -> None:
        await self.bot.db.wait()

        query = """
                SELECT
                    id,
                    (metadata->'user_id')::BIGINT AS user_id,
                    (metadata->'channel_id')::BIGINT AS channel_id,
                    metadata->>'command' AS command,
                    metadata->>'jump_url' AS jump_url
                FROM timers
                WHERE
                    event = 'cooldown_reminder'
                """
        for record in await self.bot.db.fetch(query):
            self._cooldown_reminder_exists[record['user_id']][record['command']] = CooldownReminderMetadata(
                channel_id=record['channel_id'], jump_url=record['jump_url'], timer_id=record['id'],
            )

    @command(name='help', aliases={'guide', 'start'}, hybrid=True, with_app_command=False)
    async def help(self, ctx: Context, *, entity: str = None) -> CommandResponse:
        """Sends an in-depth guide on how to use this bot.

        See `.commands` for a straightforward list of commands.
        """
        if entity:
            return await ctx.send_help(entity)

        return await self.render_guide(ctx, 'index'), Guide.view(ctx), REPLY

    help_app_command = app_commands.Group(name='help', description='Learn how to use Coined.')

    @help.define_app_command(name='guide', parent=help_app_command)
    async def help_guide(self, ctx: HybridContext) -> None:
        await ctx.invoke(ctx.command)

    @help_app_command.command(name='commands')
    @app_commands.describe(category='The category to view commands from.')
    async def help_commands(self, itx: TypedInteraction, category: str = None) -> None:
        """Browse the commands Coined has to offer."""
        ctx = await self.bot.get_context(itx)
        if category is not None:
            return await ctx.send_help(category)
        await ctx.send_help()

    @help_commands.autocomplete('category')
    async def category_autocomplete(self, _itx: TypedInteraction, current: str) -> list[app_commands.Choice]:
        return [
            app_commands.Choice(name=name.title(), value=name) for name, cog in self.bot.cogs
            if not getattr(cog, '__hidden__', True) and current in name
        ]

    @help_app_command.command(name='command')
    @app_commands.rename(cmd='command')
    @app_commands.describe(cmd='The command to learn more about.')
    async def help_cmd(self, itx: TypedInteraction, cmd: str) -> None:
        """Learn more about a specific command."""
        resolved = self.bot.get_command(cmd)
        if resolved is None:
            return await itx.response.send_message(f'Command {cmd!r} not found.', ephemeral=True)

        ctx = await self.bot.get_context(itx)
        await ctx.send_help(resolved)

    @command(aliases={'tutor', 'tut'}, hybrid=True, with_app_command=False)
    async def tutorial(self, ctx: Context) -> CommandResponse:
        """Starts an in-depth tutorial on how to use Coined."""

    @command(alias="pong", hybrid=True)
    @simple_cooldown(2, 2)
    async def ping(self, ctx: Context) -> tuple[str, Any]:
        """Pong! Sends the bot's API latency."""

        word = random.choice(self.PONG_MESSAGES)

        with PingTimer() as timer:
            if not ctx.is_interaction:
                await ctx.send(word, reference=ctx.message)
            else:
                await ctx.interaction.response.send_message(word)

        time_ms = timer.time * 1000
        return f'{word} ({time_ms:.2f} ms)', EDIT

    @command(hybrid=True)
    @simple_cooldown(1, 3)
    async def uptime(self, ctx: Context) -> tuple[str, Any]:
        """Shows the bot's uptime."""

        startup = ctx.bot.startup_timestamp
        return f'I have been online since {format_dt(startup)} ({format_dt(startup, "R")}).', REPLY

    @command(alias='link', hybrid=True)
    @simple_cooldown(2, 2)
    async def invite(self, ctx: Context) -> tuple[str, discord.ui.View, Any]:
        """Gives you a link to invite the bot to your server."""
        link = oauth_url(
            ctx.bot.user.id,
            permissions=discord.Permissions(default_permissions),
            scopes=['bot', 'applications.commands'],
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Invite me to your server!', url=link))
        view.add_item(discord.ui.Button(label='Join our offical Discord server!', url=support_server))
        view.add_item(discord.ui.Button(label='Visit our website!', url=website))
        view.add_item(discord.ui.Button(
            label='Vote for Coined to earn free crates!', url=f'https://top.gg/bot/{ctx.bot.user.id}',
        ))

        return 'For a direct text link, right click one of the buttons below and click "Copy Link"', view, REPLY

    @command(aliases={'v', 'topgg', 'dbl'}, hybrid=True)
    @simple_cooldown(2, 2)
    async def vote(self, ctx: Context) -> CommandResponse:
        """Gives you a link to vote for the bot on top.gg."""
        try:
            timeout = ClientTimeout(total=2)
            async with (
                ctx.typing(),
                ctx.bot.session.get('https://top.gg/api/weekend', timeout=timeout) as response
            ):
                response.raise_for_status()
                data = await response.json()
                is_weekend = data['is_weekend']
        # if the API is down, calculate weekend by definition in top.gg docs (Friday-Sunday UTC)
        except asyncio.TimeoutError:
            is_weekend = ctx.now.weekday() in (4, 5, 6)

        item = Items.epic_crate if is_weekend else Items.voting_crate
        view = UserView(ctx.author)
        view.add_item(discord.ui.Button(
            label=f'Vote for Coined to earn {item.singular} {item.name}',
            url=f'https://top.gg/bot/{ctx.bot.user.id}/vote',
            emoji=item.emoji,
        ))

        embed = discord.Embed(
            color=Colors.primary, description=f'Claim {item.get_sentence_chunk()} just for voting!', timestamp=ctx.now,
        )
        embed.set_author(name='Vote for Coined!', icon_url=ctx.author.display_avatar)
        embed.set_thumbnail(url=ctx.bot.user.display_avatar)

        record = await ctx.db.get_user_record(ctx.author.id)
        if record.last_dbl_vote is not None:
            vote_again = record.last_dbl_vote + datetime.timedelta(hours=12)
            if vote_again > ctx.now:
                embed.add_field(
                    name='\N{ALARM CLOCK} It seems like you already voted today.',
                    value=f'You can vote again {format_dt(vote_again, "R")}!',
                    inline=True,
                )
                button = discord.ui.Button(
                    label='Remind me when I can vote again',
                    emoji='\N{ALARM CLOCK}',
                    style=discord.ButtonStyle.primary,
                )
                button.callback = functools.partial(self._vote_button_callback, vote_again)
                view.add_item(button)

        if is_weekend:
            embed.add_field(
                name='\U0001f525 **Weekend Bonus:** Votes are doubled!',
                value='You will receive an epic crate instead of a standard voting crate this weekend!',
                inline=False,
            )

        s = '' if record.votes_this_month == 1 else 's'
        embed.add_field(
            name='\U0001f4c8 Vote Count',
            value=f'You have voted for Coined **{record.votes_this_month} time{s}** this month.',
            inline=False,
        )
        try:
            next_milestone = min(filter(lambda n: n > record.votes_this_month, VOTE_REWARDS))
            next_reward = VOTE_REWARDS[next_milestone]
        except ValueError:
            pass
        else:
            remaining = next_milestone - record.votes_this_month
            s = '' if remaining == 1 else 's'
            embed.add_field(
                name=f'\U0001f381 Next Milestone: {next_milestone} votes ({remaining} vote{s} left)',
                value=f'Upon reaching this milestone, you will receive:\n{next_reward}',
                inline=False,
            )
        embed.set_footer(text='Votes reset at the beginning of each month.')
        return embed, view, REPLY

    async def _vote_button_callback(self, vote_again: datetime.datetime, interaction: TypedInteraction) -> None:  # noqa
        await interaction.response.send_message(
            'Voting reminders are been disabled for now (they\'re a bit buggy).', ephemeral=True,
        )
        # FIXME
        # await self.bot.timers.create(
        #     when=vote_again,
        #     event='vote_reminder',
        #     metadata={
        #         'user_id': interaction.user.id,
        #         'channel_id': interaction.channel.id,
        #         'jump_url': interaction.message.jump_url,
        #     },
        # )
        # await interaction.response.send_message(
        #     f"Alright, I'll remind you to vote again {format_dt(vote_again, 'R')}!",
        #     ephemeral=True,
        # )

    @Cog.listener()
    async def on_vote_reminder_timer_complete(self, timer: Timer) -> None:
        channel = self.bot.get_partial_messageable(timer.metadata['channel_id'])

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label='Vote for Coined',
            url=f'https://top.gg/bot/{self.bot.user.id}/vote',
        ))
        view.add_item(discord.ui.Button(label='Jump to Context', url=timer.metadata['jump_url']))

        await channel.send(
            f'Hey <@{timer.metadata["user_id"]}>, you can vote again!',
            allowed_mentions=discord.AllowedMentions(users=True),
            view=view,
        )

    @staticmethod
    async def _send_settings(ctx: Context):
        record = await ctx.db.get_user_record(ctx.author.id)

        fields = []
        for setting in walk_collection(Settings, Setting):
            try:
                value = record.data[setting.key]
            except KeyError:
                continue

            readable = f'{Emojis.enabled} Enabled' if value else f'{Emojis.disabled} Disabled'

            fields.append({
                'name': f'**{setting.name}** - {readable}',
                'value': f'{setting.description}\n\nToggle using `{ctx.prefix}settings {setting.key} <enabled/disabled>`',
                'inline': False,
            })

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Settings for {ctx.author.name}', icon_url=ctx.author.display_avatar)

        return Paginator(ctx, FieldBasedFormatter(embed, fields)), REPLY

    @group(aliases={'setting', 'set', 'config', 'conf'}, hybrid=True, fallback='set', expand_subcommands=True)  # TODO: add autocomplete
    @simple_cooldown(2, 2)
    async def settings(self, ctx: Context, setting: query_setting = None, value: better_bool = None):
        """View your current settings and/or change them."""
        if setting is None:
            return await self._send_settings(ctx)

        record = await ctx.db.get_user_record(ctx.author.id)

        if value is None:
            try:
                value = record.data[setting.key]
            except KeyError:
                return 'Unknown setting', BAD_ARGUMENT

            readable = f'{Emojis.enabled} Enabled' if value else f'{Emojis.disabled} Disabled'
            return f'{setting.name} is currently **{readable}**.', REPLY

        await setting.set(ctx, value)

    MENTION_REGEX: re.Pattern[str] = re.compile(r'<@!?\d+>')

    @settings.group(aliases=('pf', 'prefixes', 'pref'), hybrid=True, fallback='list')
    async def prefix(self, ctx: Context) -> CommandResponse:
        """View your server's prefixes for traditional prefix commands."""
        record = await self.bot.db.get_guild_record(ctx.guild.id)
        prefixes = record.prefixes
        if not prefixes:
            return (
                f'No prefixes set for this server. Add one with `{ctx.clean_prefix}prefix add <prefix>`.\n'
                '*I will always respond to mentions.*'
            ), REPLY

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.description = '\n'.join('- ' + discord.utils.escape_mentions(pf) for pf in prefixes)

        embed.set_author(name=f'Prefixes for {ctx.guild.name}', icon_url=ctx.guild.icon)
        embed.set_footer(text=pluralize(f'{len(prefixes)} prefix(es)'))

        message = '*I will always respond to mentions.*'
        return message, embed, REPLY

    @prefix.command(
        'add',
        aliases=('create', '+', 'append', 'new', 'update'), user_permissions=('manage_guild',),
        hybrid=True, with_app_command=False,
    )
    async def prefix_add(self, ctx: Context, *prefixes: str) -> CommandResponse:
        """Add a prefix to your server's prefixes.

        You can separate prefixes by space to add multiple prefixes at once.
        You cannot have over 25 prefixes at once.

        Examples:
        - `{PREFIX}prefix add !`
        - `{PREFIX}prefix add "hey "`
        - `{PREFIX}prefix add ! ? "hey "`

        Arguments:
        - `prefixes`: A list of prefixes to add, separated by space. If you want a space in your prefix surround it with quotes.
        """
        if not prefixes:
            return 'Please specify prefixes to add.', BAD_ARGUMENT

        record = await self.bot.db.get_guild_record(ctx.guild.id)
        if len(record.prefixes) + len(prefixes) > 25:
            return 'You cannot have more than 25 prefixes at once.', BAD_ARGUMENT

        if any(self.MENTION_REGEX.search(prefix) for prefix in prefixes):
            return 'You cannot have mentions in your prefixes.', BAD_ARGUMENT

        if any(len(prefix) > 100 for prefix in prefixes):
            return 'Prefixes cannot be longer than 100 characters.', BAD_ARGUMENT

        record.prefixes.extend(prefixes)
        await record.update(prefixes=list(set(record.prefixes)))

        if len(prefixes) == 1:
            return f'Added {prefixes[0]!r} as a prefix.', REPLY

        return f'Added {len(prefixes)} prefixes.', REPLY

    @prefix_add.define_app_command()
    @app_commands.describe(prefix='The prefix to add.')
    async def prefix_add_app_command(self, ctx, prefix: str) -> None:
        await ctx.invoke(ctx.command, prefix)

    @prefix.command(
        'remove',
        aliases=('delete', '-', 'del', 'rm'),
        user_permissions=('manage_guild',),
        hybrid=True, with_app_command=False,
    )
    async def prefix_remove(self, ctx: Context, *prefixes: str) -> CommandResponse:
        """Remove a prefix from your server's prefixes.

        You can separate prefixes by space to remove multiple prefixes at once.

        Examples:
        - `{PREFIX}prefix remove !`
        - `{PREFIX}prefix remove "hey "`
        - `{PREFIX}prefix remove ! ? "hey "`

        Arguments:
        - `prefixes`: A list of prefixes to remove, separated by space. If there is a space in a prefix surround it with quotes.
        """
        if not prefixes:
            return 'Please specify prefixes to remove.', BAD_ARGUMENT

        record = await self.bot.db.get_guild_record(ctx.guild.id)
        updated = [prefix for prefix in record.prefixes if prefix not in prefixes]

        if len(updated) == len(record.prefixes):
            return 'No prefixes were removed. (None of your prefixes were valid)', REPLY

        diff = len(record.prefixes) - len(updated)
        await record.update(prefixes=updated)

        if len(prefixes) == 1:
            return f'Removed prefix {prefixes[0]!r}.', REPLY

        return f'Removed {diff} prefixes.', REPLY

    @prefix_remove.define_app_command()
    @app_commands.describe(prefix='The prefix to remove.')
    async def prefix_remove_app_command(self, ctx, prefix: str) -> None:
        await ctx.invoke(ctx.command, prefix)

    @prefix_remove.autocomplete('prefix')
    async def prefix_remove_autocomplete(self, itx: TypedInteraction, current: str) -> list[app_commands.Choice]:
        record = await self.bot.db.get_guild_record(itx.guild_id)
        return [
            app_commands.Choice(name=cutoff(prefix, 50), value=prefix)
            for prefix in record.prefixes if prefix.startswith(current)
        ]

    @prefix.command('clear', alias='wipe', user_permissions=('manage_guild',), hybrid=True)
    async def prefix_clear(self, ctx: Context) -> CommandResponse:
        """Clear all of your server's prefixes."""
        record = await self.bot.db.get_guild_record(ctx.guild.id)
        if not record.prefixes:
            return 'No prefixes to clear.', REPLY

        if not await ctx.confirm(
            'Are you sure you want to clear all of your prefixes?\n'
            f'If so, you *must* prefix all commands with my mention ({ctx.bot.user.mention}) in order to use commands.',
            reference=ctx.message,
            delete_after=True,
        ):
            return 'Cancelled.', REPLY

        before = len(record.prefixes)
        await record.update(prefixes=[])

        return pluralize(f'Removed {before} prefix(es).'), REPLY

    @prefix.command(
        'overwrite',
        aliases=('set', 'override'),
        user_permissions=('manage_guild',),
        hybrid=True, with_app_command=False,
    )
    async def prefix_overwrite(self, ctx: Context, *prefixes: str) -> CommandResponse:
        """Removes your server's previous prefixes and replaces them with the specified ones.

        You can separate prefixes by space to set multiple prefixes at once.

        Examples:
        - `{PREFIX}prefix overwrite !`
        - `{PREFIX}prefix overwrite "hey "`
        - `{PREFIX}prefix overwrite ! ? "hey "`

        Arguments:
        - `prefixes`: A list of prefixes to set, separated by space. If there is a space in a prefix surround it with quotes.
        """
        if not prefixes:
            return 'Please specify prefixes to set.', BAD_ARGUMENT

        prefixes = list(set(prefixes))

        if len(prefixes) > 25:
            return 'You cannot have more than 25 prefixes at once.', BAD_ARGUMENT

        if any(self.MENTION_REGEX.search(prefix) for prefix in prefixes):
            return 'You cannot have mentions in your prefixes.', BAD_ARGUMENT

        if any(len(prefix) > 100 for prefix in prefixes):
            return 'Prefixes cannot be longer than 100 characters.', BAD_ARGUMENT

        record = await self.bot.db.get_guild_record(ctx.guild.id)
        await record.update(prefixes=prefixes)

        if len(prefixes) == 1:
            return f'Set {prefixes[0]!r} as the only prefix.', REPLY

        return f'Set {len(prefixes)} prefixes.', REPLY

    @prefix_overwrite.define_app_command()
    @app_commands.describe(prefix='The new prefix.')
    async def prefix_overwrite_app_command(self, ctx, prefix: str) -> None:
        await ctx.invoke(ctx.command, prefix)

    @group(aliases={'cd', 'cds', 'cooldown'}, hybrid=True, fallback='list', expand_subcommands=True)
    @simple_cooldown(2, 3)
    async def cooldowns(self, ctx: Context) -> CommandResponse:
        """View all pending cooldowns."""
        lines = []
        active_reminders = self._cooldown_reminder_exists[ctx.author.id]
        for cmd in ctx.bot.commands:
            retry_after = await _get_retry_after(ctx, cmd)
            if not retry_after:
                continue

            timestamp = ctx.now + datetime.timedelta(seconds=retry_after)
            indicator = '\u23f0' if cmd.qualified_name in active_reminders else ''
            lines.append((f'- **{cmd.qualified_name}** ({format_dt(timestamp, "R")}) {indicator}', retry_after))

        if not lines:
            return 'No pending cooldowns.', REPLY

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Pending cooldowns for {ctx.author.name}', icon_url=ctx.author.avatar)

        lines = [line for line, _ in sorted(lines, key=lambda x: x[1])]
        formatter = LineBasedFormatter(embed, lines)
        message = (
            f'A \u23f0 next to an entry indicates you have a cooldown reminder set for that command.'
            if active_reminders else ''
        )
        return message, Paginator(ctx, formatter), REPLY

    # noinspection PyShadowingNames
    @cooldowns.command(
        'remind', aliases={'reminder', 'notify', 'remindme', 'rm', 'rem'}, hybrid=True, with_app_command=False,
    )
    @simple_cooldown(2, 5)
    async def cooldowns_remind(self, ctx: Context, *, command: CommandConverter) -> CommandResponse:
        """Reminds you when a command is available to be used again."""
        existing = self._cooldown_reminder_exists[ctx.author.id]
        if metadata := existing.get(command.qualified_name):
            view = CooldownReminderOptions(ctx, command.qualified_name, metadata)
            return f'You already have a cooldown reminder set for `{command.qualified_name}`.', view, REPLY

        retry_after = await _get_retry_after(ctx, command)
        if not retry_after:
            return 'That command is not on cooldown.', REPLY

        timestamp = ctx.now + datetime.timedelta(seconds=retry_after)
        formatted = format_dt(timestamp, "R")
        if retry_after < 30:
            return f'You can use that command {formatted}, be patient', REPLY

        message = await ctx.reply(
            f'Alright {ctx.author.mention}, I will remind you in this channel when you can use '
            f'`{command.qualified_name}` again ({formatted}).',
        )
        timer = await ctx.bot.timers.create(
            timestamp,
            'cooldown_reminder',
            channel_id=ctx.channel.id,
            user_id=ctx.author.id,
            command=command.qualified_name,
            jump_url=message.jump_url,
        )
        existing[command.qualified_name] = CooldownReminderMetadata.from_timer(timer)

    @cooldowns_remind.define_app_command()
    @app_commands.rename(cmd='command')
    @app_commands.describe(cmd='The command to remind you about.')
    async def cooldowns_remind_app_command(self, ctx: HybridContext, cmd: str):
        if cmd := ctx.bot.get_command(cmd):
            return await ctx.invoke(ctx.command, command=cmd)  # type: ignore

        await ctx.reply(f'Unknown command {cmd!r}.', ephemeral=True)

    @cooldowns_remind.autocomplete('cmd')
    @help_cmd.autocomplete('cmd')
    async def command_autocomplete(self, _: TypedInteraction, current: str) -> list[Choice[str]]:
        current = current.lower()
        return [
            Choice(name=cmd.qualified_name, value=cmd.qualified_name)
            for cmd in self.bot.walk_commands()
            if cmd.qualified_name.startswith(current) or cmd.name.startswith(current)
        ]

    @Cog.listener()
    async def on_cooldown_reminder_timer_complete(self, timer: Timer) -> None:
        self._cooldown_reminder_exists[user_id := timer.metadata['user_id']].pop(
            qualname := timer.metadata['command'], None,
        )
        channel = self.bot.get_partial_messageable(timer.metadata['channel_id'])
        try:
            await channel.send(f'You can use `{qualname}` again now, <@{user_id}>!')
        except discord.HTTPException:
            pass


class CooldownReminderOptions(UserView):
    # noinspection PyShadowingNames
    def __init__(self, ctx: Context, command: str, record: CooldownReminderMetadata) -> None:
        super().__init__(ctx.author, timeout=60)
        self.ctx = ctx
        self.cog: Miscellaneous = ctx.cog  # type: ignore
        self.command = command
        self.record = record

        if record.channel_id == ctx.channel.id:
            self.remove_item(self.move)

        self.add_item(discord.ui.Button(
            label='Jump to context',
            style=discord.ButtonStyle.link,
            url=record.jump_url,
        ))

    @discord.ui.button(label='Move reminder to this channel', style=discord.ButtonStyle.primary, emoji='\U0001f4e5')
    async def move(self, interaction: TypedInteraction, _: discord.ui.Button) -> None:
        query = "UPDATE timers SET metadata = jsonb_set(metadata, '{channel_id}', to_jsonb($1::TEXT)) WHERE id = $2"
        await self.ctx.db.execute(query, str(self.ctx.channel.id), self.record.timer_id)
        # horrible boilerplate
        self.cog._cooldown_reminder_exists[self.ctx.author.id][self.command] = self.record = (
            self.record._replace(channel_id=self.ctx.channel.id)
        )
        await interaction.response.edit_message(
            content=f'Moved reminder for `{self.command}` to {self.ctx.channel.mention}.',
            view=None,
        )

    @discord.ui.button(label='Cancel reminder', style=discord.ButtonStyle.danger, emoji='\U0001f5d1')
    async def cancel(self, interaction: TypedInteraction, _: discord.ui.Button) -> None:
        await self.ctx.bot.timers.end_timer(
            timer=await self.ctx.bot.timers.get_timer(self.record.timer_id),
            dispatch=False,
            cascade=True,
        )
        del self.cog._cooldown_reminder_exists[self.ctx.author.id][self.command]
        await interaction.response.edit_message(content=f'Cancelled reminder for `{self.command}`.', view=None)

    async def on_timeout(self) -> None:
        await self.ctx.send('Timed out.', edit=True, view=None)


setup = Miscellaneous.simple_setup

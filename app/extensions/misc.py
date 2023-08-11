from __future__ import annotations

import datetime
import functools
import random
from collections import defaultdict
from typing import Any, NamedTuple, TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.utils import format_dt, oauth_url

from app.core import BAD_ARGUMENT, Bot, Cog, Context, EDIT, REPLY, command, group, simple_cooldown
from app.core.timers import Timer
from app.data.items import Items
from app.data.settings import Setting, Settings
from app.util.common import converter, walk_collection
from app.util.converters import better_bool, query_setting
from app.util.pagination import FieldBasedFormatter, LineBasedFormatter, Paginator
from app.util.structures import Timer as PingTimer
from app.util.types import CommandResponse, TypedInteraction
from app.util.views import UserView
from config import Colors, Emojis

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
        return bucket.get_retry_after()
    return 0.0


class CooldownReminderMetadata(NamedTuple):
    channel_id: int
    jump_url: str | None
    timer_id: int

    @classmethod
    def from_timer(cls: type[Self], timer: Timer) -> Self:
        metadata = timer.metadata
        return cls(channel_id=metadata['channel_id'], jump_url=metadata['jump_url'], timer_id=timer.id)


class Miscellaneous(Cog):
    """Miscellaneous commands."""

    emoji = '<:thumbs_up:1131835741358530640>'

    PONG_MESSAGES = (
        'Pong.',
        'Pong!',
        'Pong?',
        'Pong!?',
    )

    SUPPORT_SERVER = 'https://discord.gg/BjzrQZjFwk'  # caif
    # SUPPORT_SERVER = 'https://discord.gg/bpnedYgFVd'  # unnamed bot testing

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._cooldown_reminder_exists = defaultdict[int, dict[str, CooldownReminderMetadata]](dict)
        self.__cooldown_reminder_fetch_task = self.bot.loop.create_task(self._fetch_cooldown_reminders())

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
            ctx.bot.user.id, permissions=discord.Permissions(414531833025), scopes=['bot', 'applications.commands'],
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Invite me to your server!', url=link))
        view.add_item(discord.ui.Button(label='Join our offical Discord server!', url=support_server))
        view.add_item(discord.ui.Button(
            label='Vote for Coined to earn free crates!', url=f'https://top.gg/bot/{ctx.bot.user.id}',
        ))

        return 'For a direct text link, right click one of the buttons below and click "Copy Link"', view, REPLY

    @command(aliases={'v', 'topgg', 'dbl'}, hybrid=True)
    @simple_cooldown(2, 2)
    async def vote(self, ctx: Context) -> CommandResponse:
        """Gives you a link to vote for the bot on top.gg."""

        async with ctx.bot.session.get('https://top.gg/api/weekend') as response:
            response.raise_for_status()
            data = await response.json()
            is_weekend = data['is_weekend']

        item = Items.epic_crate if is_weekend else Items.voting_crate
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label=f'Vote for Coined to earn {item.singular} {item.name}',
            url=f'https://top.gg/bot/{ctx.bot.user.id}/vote',
            emoji=item.emoji,
        ))

        extra = ''
        record = await ctx.db.get_user_record(ctx.author.id)
        if record.last_dbl_vote is not None:
            vote_again = record.last_dbl_vote + datetime.timedelta(hours=12)
            if vote_again > ctx.now:
                extra += (
                    f'\n\u23eb *It seems like you already voted today. '
                    f'You can vote again {format_dt(vote_again, "R")}!*'
                )
                button = discord.ui.Button(
                    label='Remind me when I can vote again',
                    emoji='\N{ALARM CLOCK}',
                    style=discord.ButtonStyle.primary,
                )
                button.callback = functools.partial(self._vote_button_callback, vote_again)
                view.add_item(button)

        if is_weekend:
            extra += (
                '\n\U0001f525 **BONUS:** You will receive an epic crate instead of a standard voting crate this weekend!'
            )

        return f'Claim {item.get_sentence_chunk()} just for voting!{extra}', view, REPLY

    async def _vote_button_callback(self, vote_again: datetime.datetime, interaction: TypedInteraction) -> None:
        await self.bot.timers.create(
            when=vote_again,
            event='vote_reminder',
            metadata={
                'user_id': interaction.user.id,
                'channel_id': interaction.channel.id,
                'jump_url': interaction.message.jump_url,
            },
        )
        await interaction.response.send_message(
            f"Alright, I'll remind you to vote again {format_dt(vote_again, 'R')}!",
            ephemeral=True,
        )

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
        embed.set_author(name=f'Settings for {ctx.author.name}', icon_url=ctx.author.avatar.url)

        return Paginator(ctx, FieldBasedFormatter(embed, fields)), REPLY

    @command(aliases={'setting', 'set', 'config', 'conf'}, hybrid=True)  # TODO: add autocomplete
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
            return await ctx.full_invoke(command=cmd)  # type: ignore

        await ctx.reply(f'Unknown command {cmd!r}.', ephemeral=True)

    @cooldowns_remind.autocomplete('cmd')
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

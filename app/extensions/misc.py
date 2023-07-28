from __future__ import annotations

import datetime
import random
from typing import Any, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import format_dt, oauth_url

from app.core import BAD_ARGUMENT, Cog, Context, EDIT, REPLY, command, group, simple_cooldown
from app.core.timers import Timer
from app.data.settings import Setting, Settings
from app.util.common import converter, walk_collection
from app.util.converters import better_bool, query_setting
from app.util.pagination import FieldBasedFormatter, LineBasedFormatter, Paginator
from app.util.structures import Timer as PingTimer
from app.util.types import CommandResponse
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Command


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
        view.add_item(discord.ui.Button(label='Click here to invite me to your server!', url=link))
        view.add_item(discord.ui.Button(label='Click here to join our offical Discord server!', url=self.SUPPORT_SERVER))

        return 'For a direct text link, right click one of the buttons below and click "Copy Link"', view, REPLY

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

    @command(aliases={'setting', 'set', 'config', 'conf'})
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
                return 'Unknown key', BAD_ARGUMENT

            readable = f'{Emojis.enabled} Enabled' if value else f'{Emojis.disabled} Disabled'
            return f'{setting.name} is currently **{readable}**.', REPLY

        await setting.set(ctx, value)

    @group(aliases={'cd', 'cds', 'cooldown'})
    @simple_cooldown(2, 3)
    async def cooldowns(self, ctx: Context) -> CommandResponse:
        """View all pending cooldowns."""
        lines = []
        for cmd in ctx.bot.commands:
            retry_after = await _get_retry_after(ctx, cmd)
            if not retry_after:
                continue

            timestamp = ctx.now + datetime.timedelta(seconds=retry_after)
            lines.append((f'- **{cmd.qualified_name}** ({format_dt(timestamp, "R")})', retry_after))

        if not lines:
            return 'No pending cooldowns.', REPLY

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Pending cooldowns for {ctx.author.name}', icon_url=ctx.author.avatar)

        lines = [line for line, _ in sorted(lines, key=lambda x: x[1])]
        formatter = LineBasedFormatter(embed, lines)
        return Paginator(ctx, formatter)

    # noinspection PyShadowingNames
    @cooldowns.command('remind', aliases={'reminder', 'notify', 'remindme', 'rm', 'rem'})
    @simple_cooldown(2, 5)
    async def cooldowns_remind(self, ctx: Context, *, command: CommandConverter) -> CommandResponse:
        """Reminds you when a command is available to be used again."""
        retry_after = await _get_retry_after(ctx, command)
        if not retry_after:
            return 'That command is not on cooldown.', REPLY

        timestamp = ctx.now + datetime.timedelta(seconds=retry_after)
        formatted = format_dt(timestamp, "R")
        if retry_after < 30:
            return f'You can use that command {formatted}, be patient', REPLY

        await ctx.bot.timers.create(
            timestamp,
            'cooldown_reminder',
            channel_id=ctx.channel.id,
            user_id=ctx.author.id,
            command=command.qualified_name,
        )
        return (
            f'Alright, I will remind you in this channel when you can use `{command.qualified_name}` again ({formatted}).',
            REPLY,
        )

    @Cog.listener()
    async def on_cooldown_reminder_timer_complete(self, timer: Timer) -> None:
        channel = self.bot.get_partial_messageable(timer.metadata['channel_id'])
        try:
            await channel.send(f'You can use `{timer.metadata["command"]}` again now, {timer.metadata["user_id"]}!')
        except discord.HTTPException:
            pass


setup = Miscellaneous.simple_setup

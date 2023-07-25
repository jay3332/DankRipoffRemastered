from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

import discord
from discord.utils import format_dt, oauth_url

from app.core import BAD_ARGUMENT, Cog, Context, EDIT, REPLY, command, simple_cooldown
from app.data.settings import Setting, Settings
from app.util.common import walk_collection
from app.util.converters import better_bool, query_setting
from app.util.pagination import FieldBasedFormatter, Paginator
from app.util.structures import Timer
from config import Colors, Emojis

if TYPE_CHECKING:
    pass


class Miscellaneous(Cog):
    """Miscellaneous commands."""

    emoji = '<:thumbs_up:1131835741358530640>'

    PONG_MESSAGES = (
        'Pong.',
        'Pong!',
        'Pong?',
        'Pong!?',
    )

    SUPPORT_SERVER = 'https://discord.gg/bpnedYgFVd'

    @command(alias="pong")
    @simple_cooldown(2, 2)
    async def ping(self, ctx: Context) -> tuple[str, Any]:
        """Pong! Sends the bot's API latency."""

        word = random.choice(self.PONG_MESSAGES)

        with Timer() as timer:
            if not ctx.is_interaction:
                await ctx.send(word, reference=ctx.message)
            else:
                await ctx.interaction.response.send_message(word)

        time_ms = timer.time * 1000
        return f'{word} ({time_ms:.2f} ms)', EDIT

    @command()
    @simple_cooldown(1, 3)
    async def uptime(self, ctx: Context) -> tuple[str, Any]:
        """Shows the bot's uptime."""

        startup = ctx.bot.startup_timestamp
        return f'I have been online since {format_dt(startup)} ({format_dt(startup, "R")}).', REPLY

    @command(alias='link')
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


setup = Miscellaneous.simple_setup

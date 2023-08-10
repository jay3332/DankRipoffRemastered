from __future__ import annotations

import functools
import re
from typing import Any

import discord
from discord.ext import commands
from discord.ext.ipc import ClientPayload, Server
from discord.utils import format_dt

from app.core import Cog, Command, Context
from app.core.flags import FlagMeta
from app.core.helpers import ActiveTransactionLock
from app.data.items import Items
from app.util.ansi import AnsiColor, AnsiStringBuilder
from app.util.common import humanize_duration, pluralize
from app.util.views import StaticCommandButton
from config import Colors, guilds_channel, votes_channel


class Events(Cog):
    __hidden__ = True

    @discord.utils.cached_property
    def _cooldowns_remind_command(self) -> Any:
        return self.bot.get_command('cooldowns remind')

    @Cog.listener()
    async def on_command_error(self, ctx: Context, error: Exception) -> Any:
        # sourcery no-metrics
        error = getattr(error, 'original', error)

        if isinstance(error, commands.BadUnionArgument):
            error = error.errors[0]

        blacklist = (
            commands.CommandNotFound,
            commands.CheckFailure,
        )
        if isinstance(error, blacklist):
            return

        respond = functools.partial(ctx.send, reference=ctx.message, delete_after=30, ephemeral=True)

        if isinstance(error, commands.BadArgument):
            view = None
            if isinstance(error, ActiveTransactionLock) and error.lock.jump_url is not None:
                view = discord.ui.View().add_item(
                    discord.ui.Button(label='Jump to Transaction', url=error.lock.jump_url),
                )

            ctx.command.reset_cooldown(ctx)
            return await respond(error, view=view)

        if isinstance(error, commands.MaxConcurrencyReached):
            # noinspection PyUnresolvedReferences
            return await respond(
                pluralize(f'Calm down there! This command can only be used {error.number} time(s) at once per {error.per.name}.'),
            )

        if isinstance(error, discord.NotFound) and error.code == 10062:
            return

        if isinstance(error, commands.CommandOnCooldown):
            command = ctx.command

            embed = discord.Embed(color=Colors.error, timestamp=ctx.now)
            embed.set_author(name='Command on cooldown!', icon_url=ctx.author.avatar.url)
            embed.description = getattr(command.callback, '__cooldown_message__', 'Please wait before using this command again.')

            default = pluralize(f'{error.cooldown.rate} time(s) per {humanize_duration(error.cooldown.per)}')

            embed.add_field(name='Try again after', value=humanize_duration(error.retry_after))
            embed.add_field(name='Default cooldown', value=default)

            view = None
            if error.retry_after > 30:
                view = discord.ui.View(timeout=60).add_item(
                    StaticCommandButton(
                        command=self._cooldowns_remind_command,
                        command_kwargs={'command': command},
                        label='Remind me when I can use this command again',
                        emoji='\u23f0',
                        style=discord.ButtonStyle.primary,
                    )
                )

            return await respond(embed=embed, view=view)

        if isinstance(error, (commands.ConversionError, commands.MissingRequiredArgument, commands.BadLiteralArgument)):
            ctx.command.reset_cooldown(ctx)
            param = ctx.current_parameter
        elif isinstance(error, commands.MissingRequiredArgument):
            param = error.param
        else:
            await ctx.send(f'panic!({error})', reference=ctx.message)
            raise error

        builder = AnsiStringBuilder()
        builder.append('Attempted to parse command signature:').newline(2)
        builder.append('    ' + ctx.clean_prefix, color=AnsiColor.white, bold=True)

        if ctx.invoked_parents and ctx.invoked_subcommand:
            invoked_with = ' '.join((*ctx.invoked_parents, ctx.invoked_with))
        elif ctx.invoked_parents:
            invoked_with = ' '.join(ctx.invoked_parents)
        else:
            invoked_with = ctx.invoked_with

        builder.append(invoked_with + ' ', color=AnsiColor.green, bold=True)

        command = ctx.command
        signature = Command.ansi_signature_of(command)
        builder.extend(signature)
        signature = signature.raw

        if match := re.search(
            fr"[<\[](--)?{re.escape(param.name)}((=.*)?| [<\[]\w+(\.{{3}})?[>\]])(\.{{3}})?[>\]](\.{{3}})?",
            signature,
        ):
            lower, upper = match.span()
        elif isinstance(param.annotation, FlagMeta):
            param_store = command.params
            old = command.params.copy()

            flag_key, _ = next(filter(lambda p: p[1].annotation is command.custom_flags, param_store.items()))

            del param_store[flag_key]
            lower = len(command.raw_signature) + 1

            command.params = old
            del param_store

            upper = len(command.signature) - 1
        else:
            lower, upper = 0, len(command.signature) - 1

        builder.newline()

        offset = len(ctx.clean_prefix) + len(invoked_with)  # noqa
        content = f'{" " * (lower + offset + 5)}{"^" * (upper - lower)} Error occured here'
        builder.append(content, color=AnsiColor.gray, bold=True).newline(2)
        builder.append(str(error), color=AnsiColor.red, bold=True)

        if invoked_with != ctx.command.qualified_name:
            builder.newline(2)
            builder.append('Hint: ', color=AnsiColor.white, bold=True)

            builder.append('command alias ')
            builder.append(repr(invoked_with), color=AnsiColor.cyan, bold=True)
            builder.append(' points to ')
            builder.append(ctx.command.qualified_name, color=AnsiColor.green, bold=True)
            builder.append(', is this correct?')

        ansi = builder.ensure_codeblock().dynamic(ctx)
        await ctx.send(f'Could not parse your command input properly:\n{ansi}', reference=ctx.message, ephemeral=True)

    @Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Log minimal information about a guild to a private channel when the bot joins it, for security purposes only.

        What is logged:
        - Guild ID, name, and description
        - Guild owner ID and name
        - Member count

        This is outlined in the bot's privacy policy.
        """
        channel = self.bot.get_partial_messageable(guilds_channel)
        embed = discord.Embed(
            title=guild.name,
            description=guild.description,
            color=Colors.success,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=guild.icon)
        embed.set_author(name='Chat, we got a new guild', icon_url=self.bot.user.avatar)
        embed.set_footer(text=f'Now in {len(self.bot.guilds)} guilds')
        embed.add_field(
            name='Guild',
            value=f'ID: {guild.id}\nCreated {format_dt(guild.created_at, "R")} ({format_dt(guild.created_at)})',
            inline=False,
        )
        embed.add_field(
            name='Owner',
            value=(
                f'{guild.owner} ({guild.owner_id})\n'
                f'Account created {format_dt(guild.owner.created_at, "R")} ({format_dt(guild.owner.created_at)})'
            ),
            inline=False
        )
        embed.add_field(
            name='Member Count',
            value=f'Total: {guild.member_count}\nHumans: {sum(not m.bot for m in guild.members)}',
        )
        await channel.send(embed=embed)

    @Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        channel = self.bot.get_partial_messageable(guilds_channel)
        embed = discord.Embed(
            title=guild.name,
            color=Colors.error,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=guild.icon)
        embed.set_author(name='Chat, we were removed from a guild', icon_url=self.bot.user.avatar)
        embed.set_footer(text=f'Now in {len(self.bot.guilds)} guilds')
        embed.add_field(
            name='Guild',
            value=f'ID: {guild.id}\nCreated {format_dt(guild.created_at, "R")} ({format_dt(guild.created_at)})',
            inline=False,
        )
        await channel.send(embed=embed)

    @Server.route()
    async def dbl_vote(self, data: ClientPayload) -> None:
        """Handle a vote from top.gg"""
        record = await self.bot.db.get_user_record(data.user_id)
        inventory = await record.inventory_manager.wait()
        item = Items.epic_crate if data.is_weekend else Items.voting_crate

        async with self.bot.db.acquire() as conn:
            await inventory.add_item(item, connection=conn)
            await record.notifications_manager.add_notification(
                title='Thank you for voting!',
                content=f'You received {item.get_sentence_chunk()} for your vote.',
                connection=conn,
            )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Vote for Coined', url=f'https://top.gg/bot/{self.bot.user.id}/vote'))
        weekend = (
            '\n\U0001f525 **Weekend Bonus:** Received an epic crate instead of a voting crate'
            if data.is_weekend else ''
        )

        channel = self.bot.get_partial_messageable(votes_channel)
        await channel.send(
            f'{data.user} ({data.user_id}) just voted for the bot! '
            f'They received {item.get_sentence_chunk()} for their vote. Thank you!{weekend}',
            view=view,
        )


setup = Events.simple_setup

from __future__ import annotations

from textwrap import dedent
from typing import Any, Literal, TYPE_CHECKING

import discord

from app.core import BAD_ARGUMENT, Cog, Context, NO_EXTRA, REPLY, command, group, simple_cooldown
from app.data.items import Items
from app.database import UserRecord
from app.extensions.transactions import query_item_type
from app.util.common import cutoff, progress_bar
from app.util.converters import CaseInsensitiveMemberConverter
from app.util.pagination import FieldBasedFormatter, Formatter, LineBasedFormatter, Paginator
from app.util.types import CommandResponse
from config import Colors, Emojis, multiplier_guilds

if TYPE_CHECKING:
    pass


class LeaderboardFormatter(Formatter[tuple[UserRecord, discord.Member]]):
    async def format_page(self, paginator: Paginator, entries: list[tuple[UserRecord, discord.Member]]) -> discord.Embed:
        result = []

        for i, (record, member) in enumerate(entries, start=paginator.current_page * 10):
            match i:
                case 0:
                    start = '\U0001f3c6'
                case 1:
                    start = '\U0001f948'
                case 2:
                    start = '\U0001f949'
                case _:
                    start = '<:bullet:934890293902327838>'

            result.append(f'{start} **{discord.utils.escape_markdown(str(member))}** — {Emojis.coin} {record.wallet:,}')

        embed = discord.Embed(color=Colors.primary, description='\n'.join(result), timestamp=paginator.ctx.now)
        # noinspection PyTypeChecker
        embed.set_author(name=f'Leaderboard: {paginator.ctx.guild.name}', icon_url=paginator.ctx.guild.icon)
        embed.set_footer(text=f'Page {paginator.current_page + 1}/{paginator.max_pages}')

        return embed


class Stats(Cog):
    """Useful statistical commands. These commands do not have any action behind them."""

    emoji = '\U0001f4ca'

    # noinspection PyTypeChecker
    @command(aliases={"bal", "coins", "stats", "b", "wallet"})
    @simple_cooldown(2, 5)
    async def balance(self, ctx: Context, *, user: CaseInsensitiveMemberConverter | None = None) -> tuple[discord.Embed, Any, Any | None]:
        """View your wallet and bank balance, or optionally, someone elses."""
        user = user or ctx.author
        data = await ctx.db.get_user_record(user.id)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        prestige_text = (
            f'{Emojis.get_prestige_emoji(data.prestige)} Prestige {data.prestige}' if data.prestige else 'Coins'
        )
        embed.set_author(name=f"Balance: {user}", icon_url=user.avatar)
        embed.add_field(name=prestige_text, value=dedent(f"""
            - Wallet: {Emojis.coin} **{data.wallet:,}**
            - Bank: {Emojis.coin} **{data.bank:,}**/{data.max_bank:,} *[{data.bank_ratio:.1%}]*
            - Total: {Emojis.coin} **{data.wallet + data.bank:,}**
        """))
        embed.set_thumbnail(url=user.avatar)

        return embed, REPLY, NO_EXTRA if ctx.author != user else None

    @command(aliases={'lvl', 'lv', 'l', 'xp', 'exp'})
    @simple_cooldown(2, 5)
    async def level(self, ctx: Context, *, user: CaseInsensitiveMemberConverter | None = None) -> tuple[discord.Embed, Any, Any]:
        """View your current level and experience."""
        user = user or ctx.author
        data = await ctx.db.get_user_record(user.id)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f"Level: {user}", icon_url=user.avatar.url)

        level, exp, requirement = data.level_data
        embed.add_field(
            name=f"Level {level:,}",
            value=f'{exp:,}/{requirement:,} XP ({exp / requirement:.1%})\n{progress_bar(exp / requirement)}',
        )

        return embed, REPLY, NO_EXTRA

    @command(aliases={'mul', 'ml', 'mti', 'multi', 'multipliers'})
    @simple_cooldown(2, 5)
    async def multiplier(self, ctx: Context) -> CommandResponse:
        """View a detailed breakdown of all multipliers."""
        data = await ctx.db.get_user_record(ctx.author.id)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f"Multipliers: {ctx.author}", icon_url=ctx.author.avatar.url)

        # XP Multi
        details = []
        if data.base_exp_multiplier:
            details.append(f'- Base Multiplier\\*: +**{data.base_exp_multiplier:.1%}** (global)')
        if data.prestige:
            details.append(f'- Prestige {data.prestige}: +**{data.prestige * 25}%** (global)')
        if ctx.guild.id in multiplier_guilds:
            details.append(f'- {ctx.guild}: +**50%**')

        embed.add_field(
            name=f"Total XP Multiplier: **{data.total_exp_multiplier - 1:.1%}**",
            value='\n'.join(details) or 'No XP multipliers applied.',
            inline=False
        )
        embed.set_footer(text='\\*This multiplier is accumulated from using items like cheese')

        # Coin Multi
        details = []
        if data.prestige:
            details.append(f'- Prestige {data.prestige}: +**{data.prestige * 25}%** (global)')

        embed.add_field(
            name=f"Total Coin Multiplier: **{data.coin_multiplier - 1:.1%}**",
            value='\n'.join(details) or 'No coin multipliers applied.',
            inline=False
        )

        # Bank space growth multi
        details = []
        if data.prestige:
            details.append(f'- Prestige {data.prestige}: +**{data.prestige * 50}%** (global)')

        embed.add_field(
            name=f"Total Bank Space Growth Multiplier: **{data.bank_space_growth_multiplier - 1:.1%}**",
            value='\n'.join(details) or 'No bank space multipliers applied.',
            inline=False
        )

        return embed, REPLY

    @command(aliases={"rich", "lb", "top", "richest", "wealthiest"})
    @simple_cooldown(1, 15)
    async def leaderboard(self, ctx: Context):
        """View the richest people in terms of coins in your server.

        A few things to note:
        - This leaderboard is for *guild only*.
        - This leaderboard only shows *cached users*: if a user has not used the bot since the last startup, they will not be shown here.
        - This leaderboard shows the richest users by their *wallet.*

        This is prone to change in the future when flags are implemented. For now, there are limitations.
        """
        members = ctx.guild._members

        records = sorted(
            (
                (record, ctx.guild.get_member(key))
                for key, record in ctx.db.user_records.items()
                if key in members and record.wallet
            ),
            key=lambda r: r[0].wallet,
            reverse=True,
        )

        if not records:
            return "I don't see anyone in the cache that's in this server."

        return Paginator(ctx, LeaderboardFormatter(records, per_page=10), timeout=120), REPLY

    @command(aliases={"inv", "backpack", "items"})
    @simple_cooldown(1, 6)
    async def inventory(self, ctx: Context, *, user: CaseInsensitiveMemberConverter | None = None):
        """View your inventory, or optionally, someone elses."""
        user = user or ctx.author

        record = await ctx.db.get_user_record(user.id)
        inventory = await record.inventory_manager.wait()

        fields = [{
            'name': f'{item.display_name} — **{quantity:,}**',
            'value': f'Worth {Emojis.coin} **{item.price * quantity:,}**',
            'inline': False,
        } for item, quantity in inventory.cached.items() if quantity]

        worth = sum(item.price * quantity for item, quantity in inventory.cached.items())

        if not len(fields):
            return f'{"You currently do" if user == ctx.author else f"{user.name} currently does"} not own any items.', REPLY

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.description = dedent(f"""
            {'Your' if user == ctx.author else f"{user.name}'s"} inventory is worth {Emojis.coin} **{worth:,}**.
            Additionally, you own **{len(fields):,}** out of {len(list(Items.all())):,} unique items.
        """)
        embed.set_author(name=f'{user.name}\'s Inventory', icon_url=user.avatar.url)

        return Paginator(ctx, FieldBasedFormatter(embed, fields, per_page=5), timeout=120), REPLY, NO_EXTRA if ctx.author != user else None

    @command(aliases={"itembook", "uniqueitems", "discovered", "ib"})
    @simple_cooldown(2, 6)
    async def book(
        self,
        ctx: Context,
        rarity: Literal['common', 'uncommon', 'rare', 'epic', 'legendary', 'mythic', 'all'] | None = 'all',
        category: query_item_type = None,
    ):
        """View a summary of all unique items you have discovered (and what you are missing)."""
        record = await ctx.db.get_user_record(ctx.author.id)
        inventory = await record.inventory_manager.wait()
        quantity = inventory.cached.quantity_of

        rarity = rarity.lower()

        lines = [
            f'{item.get_display_name(bold=quantity(item) > 0)} ({item.rarity.name.title()}) x{quantity(item):,}'
            for item in Items.all()
            if rarity in ('all', item.rarity.name.lower())
            and (category is None or item.type is category)
        ]

        count = sum(quantity > 0 for quantity in inventory.cached.values())

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name}\'s Item Book', icon_url=ctx.author.avatar.url)
        embed.description = f'You own **{count:,}** out of {len(list(Items.all())):,} unique items.'

        if rarity != 'all':
            count = sum(quantity > 0 for item, quantity in inventory.cached.items() if item.rarity.name.lower() == rarity)
            embed.description += f'\nYou have also discovered {count:,} out of {len(lines):,} **{rarity.lower()}** items.'

        return Paginator(ctx, LineBasedFormatter(embed, lines, field_name='\u200b'), timeout=120), REPLY

    @group(aliases={"notifs", "notification", "notif", "nt"})
    @simple_cooldown(1, 6)
    async def notifications(self, ctx: Context) -> tuple[str | Paginator, Any]:
        """View your notifications."""
        record = await ctx.db.get_user_record(ctx.author.id)
        notifications = await record.notifications_manager.wait()

        await record.update(unread_notifications=0)

        fields = [{
            'name': f'{idx}. {notification.title} ({discord.utils.format_dt(notification.created_at, "R")})',
            'value': cutoff(notification.content),
            'inline': False,
        } for idx, notification in enumerate(notifications.cached, start=1)]

        if not len(fields):
            return 'You currently do not have any notifications.', REPLY

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.description = (
            f'Run `{ctx.clean_prefix}notifications view <index>` to view a specific notification.\n'
            f'Likewise, run `{ctx.clean_prefix}notifications clear` to clear all notifications.'
        )
        embed.set_author(name=f'{ctx.author.name}\'s Notifications', icon_url=ctx.author.avatar.url)

        return Paginator(ctx, FieldBasedFormatter(embed, fields, per_page=5), timeout=120), REPLY

    @notifications.command(name='view', aliases={"v", "read", "info"})
    @simple_cooldown(2, 3)
    async def notifs_view(self, ctx: Context, index: int) -> tuple[discord.Embed | str, Any]:
        """View information on a specific notification."""
        if index < -1:
            return 'Notification index must be positive.', BAD_ARGUMENT

        record = await ctx.db.get_user_record(ctx.author.id)
        notifications = await record.notifications_manager.wait()
        try:
            notification = notifications.cached[index - 1]
        except IndexError:
            return 'Invalid notification index.', BAD_ARGUMENT

        embed = discord.Embed(color=Colors.primary, description=notification.content, timestamp=ctx.now)
        embed.set_author(name=notification.title, icon_url=ctx.author.avatar.url)
        embed.add_field(name='Created', value=discord.utils.format_dt(notification.created_at, "R"))

        return embed, REPLY

    @notifications.command(name='clear', aliases={"c", "wipe"})
    @simple_cooldown(1, 10)
    async def notifs_clear(self, ctx: Context) -> tuple[str, Any]:
        """Clear all of your notifications."""
        await ctx.db.execute('DELETE FROM notifications WHERE user_id = $1', ctx.author.id)

        record = await ctx.db.get_user_record(ctx.author.id)
        notifications = await record.notifications_manager.wait()
        notifications.cached.clear()

        return 'Cleared all of your notifications.', REPLY


setup = Stats.simple_setup

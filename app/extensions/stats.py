from __future__ import annotations

from textwrap import dedent
from typing import Any, TYPE_CHECKING

import discord

from app.core import Cog, Context, REPLY, command, simple_cooldown
from app.database import UserRecord
from app.util.converters import CaseInsensitiveMemberConverter
from app.util.pagination import Formatter, Paginator
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


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
                    start = f'{i + 1}.'

            result.append(f'{start} **{discord.utils.escape_markdown(str(member))}** — {Emojis.coin} {record.wallet:,}')

        embed = discord.Embed(color=Colors.primary, description='\n'.join(result), timestamp=paginator.ctx.now)
        # noinspection PyTypeChecker
        embed.set_author(name=f'Leaderboard: {paginator.ctx.guild.name}', icon_url=paginator.ctx.guild.icon)
        embed.set_footer(text=f'Page {paginator.current_page + 1}/{paginator.max_pages}')

        return embed


class Stats(Cog):
    """Useful statistical commands. These commands do not have any action behind them."""

    # noinspection PyTypeChecker
    @command(aliases={"bal", "coins", "stats", "b", "wallet"})
    @simple_cooldown(2, 5)
    async def balance(self, ctx: Context, *, user: CaseInsensitiveMemberConverter | None = None) -> tuple[discord.Embed, Any]:
        """View your wallet and bank balance, or optionally, someone elses."""
        user = user or ctx.author
        data = await ctx.db.get_user_record(user.id)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)

        embed.set_author(name=f"Balance: {user}", icon_url=user.avatar)
        embed.add_field(name="Coins", value=dedent(f"""
            Wallet: {Emojis.coin} **{data.wallet:,}**
            Bank: {Emojis.coin} **{data.bank:,}**/{data.max_bank:,} *[{data.bank_ratio * 100:.1f}%]*
            Total: {Emojis.coin} **{data.wallet + data.bank:,}**
        """))

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


def setup(bot: Bot) -> None:
    bot.add_cog(Stats(bot))

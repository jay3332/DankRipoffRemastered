from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from app.core import Cog, Context, command
from app.util.converters import CaseInsensitiveMemberConverter
from config import Colors, Emojis

if TYPE_CHECKING:
    from app.core import Bot


class Stats(Cog):
    """Useful statistical commands. These commands do not have any action behind them."""

    # noinspection PyTypeChecker
    @command(aliases={"bal", "coins", "stats", "b", "wallet"})
    async def balance(self, ctx: Context, *, user: CaseInsensitiveMemberConverter | None = None):
        """View your wallet and bank balance, or optionally, someone elses."""
        user = user or ctx.author
        data = await ctx.db.get_user_record(user.id)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)

        embed.set_author(name=f"Balance: {user}", icon_url=user.avatar)
        embed.add_field(name="Coins", value=f"""
            Wallet: {Emojis.coin} **{data.wallet:,}**
            Bank: {Emojis.coin} **{data.bank:,}**/{data.max_bank:,} *[{data.bank_ratio * 100:.1f}%]*
        """)

        return embed


def setup(bot: Bot) -> None:
    bot.add_cog(Stats(bot))

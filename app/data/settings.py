from __future__ import annotations

from typing import NamedTuple, TYPE_CHECKING

from config import Emojis

if TYPE_CHECKING:
    from app.core import Context


class Setting(NamedTuple):
    key: str
    name: str
    description: str

    async def set(self, ctx: Context, value: bool) -> None:
        record = await ctx.db.get_user_record(ctx.author.id)
        await record.update(**{self.key: value})

        new = f'{Emojis.enabled} Enabled' if value else f'{Emojis.disabled} Disabled'

        await ctx.send(f'Setting **{self.name}** set to **{new}**.', reference=ctx.message)
        await ctx.thumbs()


class Settings:
    dm_notifications = Setting(
        key='dm_notifications',
        name='DM Notifications',
        description='When enabled, I will direct message you whenever you receive a notification.',
    )

import discord
from discord.ext.commands import MemberConverter, MemberNotFound

from app.util.common import converter


@converter
async def CaseInsensitiveMemberConverter(ctx, argument):
    # This may not scale too well.
    try:
        return await MemberConverter().convert(ctx, argument)
    except MemberNotFound:
        argument = argument.lower()

        def check(member):
            return (
                member.name.lower() == argument
                or member.display_name.lower() == argument
                or str(member).lower() == argument
                or str(member.id) == argument
            )

        if found := discord.utils.find(check, ctx.guild.members):
            return found

        raise MemberNotFound(argument)

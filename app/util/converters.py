import re
from typing import Literal, Type

import discord
from discord.ext.commands import BadArgument, Converter, MemberConverter, MemberNotFound

from app.core import Context
from app.util.common import converter


def get_number(argument: str) -> int:
    argument = argument.lower().replace(",", "").replace("+", "").strip()
    if argument == "":
        raise ValueError()

    match argument[-1]:
        case 'k':
            argument = float(argument.rstrip("k")) * 1_000
        case 'm':
            argument = float(argument.rstrip("m")) * 1_000_000
        case 'b':
            argument = float(argument.rstrip("k")) * 1_000_000_000
        case _:
            if re.match(r"\de\d+", argument):
                num, exp = argument.split("e")
                num, exp = float(num), int(exp)
                argument = float(f"{num}e{exp}") if exp < 24 else 1e24

    return round(float(argument))


class NotAnInteger(Exception):
    pass


class NotEnough(Exception):
    pass


class PastMinimum(Exception):
    pass


def get_amount(total: float, minimum: int, maximum: int, arg: str) -> int:
    """Gets an amount of coins given an argument.

    Supports all/max, half, n/n fractions, and percentages. (and actual numbers, of course.)
    """
    arg = arg.lower().strip()

    if arg in ("all", "max", "a", "m"):
        amount = round(total)

    elif arg in ("half", "h"):
        amount = round(total / 2)

    elif arg.endswith("%"):
        percent = arg.rstrip('%')
        try:
            percent = float(percent) / 100
        except (TypeError, ValueError):
            raise NotAnInteger()
        else:
            amount = round(total * percent)

    elif re.match(r"[0-9.]+/[0-9.]+", arg):
        try:
            num, de = arg.split("/")
            num, de = float(num), float(de)
        except (ValueError, TypeError):
            raise NotAnInteger()
        else:
            if de == 0:
                raise ZeroDivisionError()

            amount = round(total * (num / de))
    else:
        try:
            amount = get_number(arg)
        except (ValueError, ZeroDivisionError, TimeoutError, IndexError, KeyError):
            raise NotAnInteger()

    if amount > total:
        raise NotEnough()

    if amount <= 0:
        raise NotAnInteger()

    if minimum <= amount <= maximum:
        return amount

    elif amount > maximum:
        return maximum

    raise PastMinimum()


@converter
async def CaseInsensitiveMemberConverter(ctx: Context, argument: str) -> discord.Member:
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


WITHDRAW = 0
DEPOSIT = 1


def BankTransaction(method: Literal[0, 1]) -> Type[Converter | int]:
    class Wrapper(Converter, int):
        async def convert(self, ctx: Context, arg: str) -> int:
            record = await ctx.db.get_user_record(ctx.author.id)
            _all = getattr(record, 'wallet' if method == DEPOSIT else 'bank')

            maximum = record.max_bank - record.bank if method == DEPOSIT else record.bank

            if maximum <= 0:
                raise BadArgument(
                    "You have no more space in your bank. Consider expanding it."
                    if method == DEPOSIT
                    else "You don't have any coins to withdraw."
                )

            try:
                return get_amount(_all, 0, maximum, arg)
            except NotAnInteger:
                raise BadArgument(
                    f"{'Withdraw' if method == WITHDRAW else 'Deposit'} amount must be a positive integer."
                )
            except NotEnough:
                raise BadArgument(
                    "You don't have that many coins in your wallet, get better."
                    if method == DEPOSIT
                    else "You can't withdraw more than what you actually have."
                )
            except ZeroDivisionError:
                raise BadArgument("Very funny. Division by 0.")

    return Wrapper

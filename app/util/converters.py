from __future__ import annotations

import re
from typing import Literal, NamedTuple, Type, TYPE_CHECKING

import discord
from discord.ext.commands import BadArgument, Converter, MemberConverter, MemberNotFound

from app.data.items import Item, Items
from app.util.common import converter, query_collection
from config import Emojis

if TYPE_CHECKING:
    from app.core import Context


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


class ItemAndQuantity(NamedTuple):
    item: Item
    quantity: int = 1


BUY = 'buy'
SELL = 'sell'
USE = 'use'
DROP = 'drop'


def ItemAndQuantityConverter(method: Literal[0, 1, 2]) -> Type[Converter | ItemAndQuantity]:
    class Wrapper(Converter):
        async def convert(self, ctx: Context, argument: str) -> ItemAndQuantity:
            item: Item | None = None
            quantity = '1'

            if result := try_query_item(argument):
                item = result

            elif len(split := argument.split()) > 1:
                result, quantity = ' '.join(split[:-1]), split[-1]

                if result := try_query_item(result):
                    item = result

                if not item:
                    result, quantity = ' '.join(split[1:]), split[0]
                    if result := try_query_item(result):
                        item = result

            if not item:
                raise BadArgument(f'Item "{argument}" not found.')

            if method == BUY and not item.buyable:
                raise BadArgument('This item is currently not buyable.')

            if method == SELL and not item.sellable:
                raise BadArgument('This item is not sellable.')

            if method == USE and not item.usable:
                raise BadArgument('This item is not usable.')

            if method == DROP and not item.giftable:
                raise BadArgument('This item is not giftable.')

            record = await ctx.db.get_user_record(ctx.author.id)

            if method == BUY:
                maximum = record.wallet // item.price
            else:
                inventory = await record.inventory_manager.wait()
                maximum = inventory.cached.quantity_of(item)

            try:
                quantity = get_amount(maximum, 1, maximum, quantity)

            except PastMinimum:
                raise BadArgument(f'You must {method} at least one of that item.')

            except NotAnInteger:
                raise BadArgument(f'Invalid quantity {quantity} - either what you specified yields 0, or it is not an integer.')

            except NotEnough:
                raise BadArgument(
                    'Insufficient funds - you do not have enough coins to make this purchase.'
                    if method == BUY
                    else 'You do not have that many of that item.'
                )

            except ZeroDivisionError:
                raise BadArgument('Very funny, division by 0.')

            return ItemAndQuantity(item, quantity)

    return Wrapper


def query_item(query: str, /) -> Item:
    if match := query_collection(Items, Item, query):
        return match

    raise BadArgument(f"I couldn't find a item named {query!r}.")


def try_query_item(query: str, /) -> Item | None:
    try:
        return query_item(query)
    except BadArgument:
        return None


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


def Investment(minimum: int = 500, maximum: int = 50000000) -> Type[Converter | int]:
    class Wrapper(Converter, int):
        async def convert(self, ctx: Context, arg: str) -> int:
            record = await ctx.db.get_user_record(ctx.author.id)
            _all = record.wallet

            try:
                return get_amount(_all, minimum, maximum, arg)

            except NotAnInteger:
                raise BadArgument("Investment amount must be a positive integer.")

            except NotEnough:
                raise BadArgument("You don't have that many coins.")

            except PastMinimum:
                raise BadArgument(f"The minimum investment is {Emojis.coin} **{minimum:,}**.")

            except ZeroDivisionError:
                raise BadArgument("very funny, division by zero.")

    return Wrapper


@converter
async def DropAmount(ctx: Context, arg: str) -> int:
    record = await ctx.db.get_user_record(ctx.author.id)
    _all = record.wallet

    try:
        return get_amount(_all, 1, _all, arg)

    except NotAnInteger:
        raise BadArgument("Drop amount must be a positive integer.")

    except NotEnough:
        raise BadArgument("You don't have that many coins.")

    except PastMinimum:
        raise BadArgument("Drop amount must be positive.")

    except ZeroDivisionError:
        raise BadArgument("very funny, division by zero.")


def CasinoBet(minimum: int = 200, maximum: int = 500000) -> Type[Converter | int]:
    class Wrapper(Converter, int):
        async def convert(self, ctx: Context, argument: str):
            record = await ctx.db.get_user_record(ctx.author.id)

            try:
                return get_amount(record.wallet, minimum, maximum, argument)

            except NotAnInteger:
                raise BadArgument("Bet amount must be a positive integer.")

            except NotEnough:
                raise BadArgument("You don't have that many coins.")

            except PastMinimum:
                raise BadArgument(f"The minimum bet for `{ctx.command.qualified_name}` is {Emojis.coin} **{minimum:,}**.")

            except ZeroDivisionError:
                raise BadArgument("very funny, division by zero.")

    return Wrapper

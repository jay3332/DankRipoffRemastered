from __future__ import annotations

import re
from typing import Literal, NamedTuple, Type, TYPE_CHECKING

import discord
from discord.ext.commands import BadArgument, Converter, MemberConverter, MemberNotFound

from app.data.items import Item, ItemType, Items
from app.data.recipes import Recipe, Recipes
from app.data.settings import Setting, Settings
from app.data.skills import Skill, Skills
from app.util.common import converter, query_collection, walk_collection
from config import Emojis

if TYPE_CHECKING:
    from app.core import Context

    better_bool: Type[bool]
    query_item: Type[Item]
    query_skill: Type[Skill]
    query_setting: Type[Setting]
    query_recipe: Type[Recipe]


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


def parse_quantity_and_item(argument) -> tuple[Item | None, str]:
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

    return item, quantity


def ItemAndQuantityConverter(method: Literal[0, 1, 2]) -> Type[Converter | ItemAndQuantity]:
    class Wrapper(Converter):
        async def convert(self, ctx: Context, argument: str) -> ItemAndQuantity:
            item, quantity = parse_quantity_and_item(argument)

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


def query_crop(query: str, /) -> Item:
    try:
        crop = query_item(query.removesuffix(' crop') + ' crop')
    except BadArgument:
        crop = query_item(query)

        if crop.type is not ItemType.crop:
            crop = query_item(crop.name + ' crop')

    if crop.type is not ItemType.crop:
        raise BadArgument(f'{crop.name} is not a crop.')

    return crop


def try_query_item(query: str, /) -> Item | None:
    try:
        return query_item(query)
    except BadArgument:
        return None


def query_skill(query: str, /) -> Skill:
    if match := query_collection(Skills, Skill, query):
        return match

    raise BadArgument(f"I couldn't find a skill named {query!r}.")


def query_setting(query: str, /) -> Setting:
    if match := query_collection(Settings, Setting, query):
        return match

    raise BadArgument(f"I couldn't find a setting named {query!r}.")


def query_recipe(query: str, /) -> Recipe:
    if match := query_collection(Recipes, Recipe, query):
        return match

    raise BadArgument(f"I couldn't find a craftable item/recipe named {query!r}.")


def better_bool(arg: str, /) -> bool:
    arg = arg.lower()

    if arg in {'true', 'yes', 'y', 'on', 'enable', 'enabled', 't', '1'}:
        return True

    if arg in {'false', 'no', 'n', 'off', 'disable', 'disabled', 'f', '0'}:
        return False

    raise BadArgument(f'Invalid boolean value {arg!r}')


@converter
async def RecipeConverter(ctx: Context, argument: str) -> Recipe:
    to_raise = BadArgument(
        f'Invalid craft entities given. Format your recipe by separating them with commas, e.g. `{ctx.clean_prefix}craft 3 wood, 2 iron`'
    )

    entities = map(str.strip, argument.rstrip(',').split(','))
    try:
        entities = {item: int(raw_quantity) for item, raw_quantity in map(parse_quantity_and_item, entities)}
    except ValueError:
        raise to_raise

    if None in entities:
        raise to_raise

    recipe = discord.utils.get(walk_collection(Recipes, Recipe), ingredients=entities)
    if not recipe:
        raise BadArgument(
            'Could not craft anything from that. Note that you can only craft/discover one item at once using this command. '
            f'You can craft already discovered recipes in bulk using the `{ctx.clean_prefix}recipes` command.'
        )

    return recipe


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
        raise BadArgument("Entity must be a positive integer or a valid item with an optional quantity.")

    except NotEnough:
        raise BadArgument("You don't have that many coins.")

    except PastMinimum:
        raise BadArgument("Amount must be positive.")

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

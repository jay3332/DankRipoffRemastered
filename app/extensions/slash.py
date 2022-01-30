from __future__ import annotations

from typing import Any, Callable, Type

import discord
from discord import Message, PartialMessage
from discord.application_commands import ApplicationCommand, ApplicationCommandTree, option

from app.core import Bot, Context
from app.util.common import cutoff
from app.util.types import TypedInteraction

TREE = ApplicationCommandTree(guild_id=893991611262976091)


class MakeshiftMessage(PartialMessage):
    activity = application = edited_at = reference = webhook_id = None
    attachments = components = reactions = stickers = []
    tts = False

    raw_mentions = Message.raw_mentions
    clean_content = Message.clean_content
    channel_mentions = Message.channel_mentions
    raw_role_mentions = Message.raw_role_mentions
    raw_channel_mentions = Message.raw_channel_mentions

    author: discord.User | discord.Member
    content: str

    @classmethod
    def from_interaction(
        cls,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.DMChannel | discord.Thread,
    ) -> MakeshiftMessage:
        self = cls(channel=channel, id=interaction.id)
        self.author = interaction.user

        return self


async def run_command(interaction: TypedInteraction, command: str) -> None:
    bot = interaction.client

    message = MakeshiftMessage.from_interaction(interaction, interaction.channel)
    message.content = f'{bot.user.mention} {command}'

    # noinspection PyTypeChecker
    ctx = await bot.get_context(message, cls=Context)
    ctx.interaction = interaction

    await bot.invoke(ctx)


def _make_callback(factory: Callable[[Any], str]) -> Callable[[Any], Any]:
    async def callback(self: Any, interaction: TypedInteraction) -> None:
        await run_command(interaction, factory(self))

    return callback


def _declare_simple_command(command: str, **kwargs) -> Type[ApplicationCommand]:
    if not kwargs.get('name'):
        kwargs['name'] = command

    class Wrapper(ApplicationCommand, description=command, tree=TREE, **kwargs):
        callback = _make_callback(lambda _: command)

    return Wrapper


class Help(ApplicationCommand, tree=TREE):
    """help"""
    entity: str = option(description='The specific command to get help for.')

    callback = _make_callback(lambda self: f'help {self.entity or ""}')  # TODO: make this compatible with slash commands


Ping = _declare_simple_command('ping')


class Balance(ApplicationCommand, tree=TREE):
    """balance"""
    user: discord.Member = option(description='The user to check the balance of.')

    callback = _make_callback(lambda self: f'balance {self.user.id if self.user else ""}')


class Level(ApplicationCommand, tree=TREE):
    """level"""
    user: discord.Member = option(description='The user to check the level of.')

    callback = _make_callback(lambda self: f'level {self.user.id if self.user else ""}')


class Inventory(ApplicationCommand, tree=TREE):
    """inventory"""
    user: discord.Member = option(description='The user to check the inventory of.')

    callback = _make_callback(lambda self: f'inventory {self.user.id if self.user else ""}')


Leaderboard = _declare_simple_command('leaderboard')


class Notifications(ApplicationCommand, tree=TREE):
    """Notificaton related commands"""
    List = _declare_simple_command('notifications', name='list')
    Clear = _declare_simple_command('notifications clear', name='clear')

    class View(ApplicationCommand, tree=TREE):
        """notifications view"""
        index: int = option(description='The index of the notification to view.', required=True)


Beg = _declare_simple_command('beg')
Search = _declare_simple_command('search')
Fish = _declare_simple_command('fish')
Daily = _declare_simple_command('daily')


class Withdraw(ApplicationCommand, tree=TREE):
    """withdraw"""
    amount: str = option(description='The amount of coins to withdraw.', required=True)

    callback = _make_callback(lambda self: f'withdraw {self.amount}')


class Deposit(ApplicationCommand, tree=TREE):
    """deposit"""
    amount: str = option(description='The amount of coins to deposit.', required=True)

    callback = _make_callback(lambda self: f'deposit {self.amount}')


Shop = _declare_simple_command('shop')


class ItemInfo(ApplicationCommand, name='item-info', tree=TREE):
    """ii"""
    item: str = option(description='The item to get info on.', required=True)

    callback = _make_callback(lambda self: f'ii {self.item}')


class Buy(ApplicationCommand, tree=TREE):
    """buy"""
    item: str = option(description='The item to buy.', required=True)
    quantity: str = option(description='The quantity of that item to buy.', default='1')

    callback = _make_callback(lambda self: f'buy {self.item} {self.quantity}')


class Sell(ApplicationCommand, tree=TREE):
    """sell"""
    item: str = option(description='The item to sell.', required=True)
    quantity: str = option(description='The quantity of that item to sell.', default='1')

    callback = _make_callback(lambda self: f'sell {self.item} {self.quantity}')


class Use(ApplicationCommand, tree=TREE):
    """use"""
    item: str = option(description='The item to use.', required=True)
    quantity: str = option(description='The quantity of that item to use, if applicable.', default='1')

    callback = _make_callback(lambda self: f'use {self.item} {self.quantity}')


class Drop(ApplicationCommand, tree=TREE):
    """drop"""
    entity: str = option(description='The amount of coins or items to drop.', required=True)

    callback = _make_callback(lambda self: f'drop {self.entity}')


Skills = _declare_simple_command('skills')


class SkillInfo(ApplicationCommand, name='skill-info', tree=TREE):
    """skills view"""
    skill: str = option(description='The skill to get info on.', required=True)

    callback = _make_callback(lambda self: f'skills view {self.skill}')


class BuySkill(ApplicationCommand, name='buy-skill', tree=TREE):
    """skills buy"""
    skill: str = option(description='The skill to buy.', required=True)

    callback = _make_callback(lambda self: f'skills buy {self.skill}')


def setup(bot: Bot) -> None:
    for command in TREE.commands:
        framework_command = bot.get_command(command.__application_command_description__)

        if not framework_command:
            continue

        command.__application_command_description__ = cutoff(framework_command.short_doc, 100, exact=True)

    bot.add_application_command_tree(TREE)

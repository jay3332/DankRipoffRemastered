from __future__ import annotations

from typing import Any, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core.models import Command
from app.util.ansi import AnsiColor, AnsiStringBuilder
from app.util.common import cutoff, humanize_duration, image_url_from_emoji, pluralize
from app.util.pagination import Paginator, PaginatorView, FieldBasedFormatter
from app.util.types import TypedInteraction
from app.util.views import UserView
from config import Colors

if TYPE_CHECKING:
    from app.core import Cog, Context, GroupCommand


class CogSelect(discord.ui.Select[PaginatorView]):
    def __init__(self, mapping: dict[Cog, list[Command]], *, default: Cog = None) -> None:
        super().__init__(placeholder='Select a category...', row=0)

        self.add_option(label='Home', value='Home')

        for cog in mapping:
            # TODO: emojis for cogs

            self.add_option(
                label=cog.qualified_name,
                value=cog.qualified_name,
                emoji=cog.emoji,
                description=cutoff(cog.description, max_length=50, exact=True),
                default=cog is default,
            )

        self.mapping: dict[Cog, list[Command]] = mapping
        self.cog_mapping: dict[str, Cog] = {cog.qualified_name: cog for cog in mapping}

    @staticmethod
    def get_command_fields(ctx: Context, cog: Cog) -> list[dict[str, str | bool]]:
        return HelpCommand.commands_into_fields(ctx, cog.get_commands())

    @staticmethod
    def get_base_cog_embed(ctx: Context, cog: Cog) -> discord.Embed:
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.description = f'There are {len(cog.get_commands())} commands in this category.'
        embed.set_author(name=f'Help ({cog.qualified_name}): {ctx.author.name}', icon_url=ctx.author.avatar.url)
        embed.set_footer(text=f'Run `{ctx.clean_prefix}help <command>` to get help on a specific command.')
        if emoji := getattr(cog, 'emoji', None):
            embed.set_thumbnail(url=image_url_from_emoji(emoji))

        return embed

    async def callback(self, interaction: TypedInteraction) -> None:
        ctx = self.view.paginator.ctx

        try:
            cog = self.cog_mapping[self.values[0]]
        except KeyError:
            paginator = HelpCommand.get_bot_help_paginator(ctx, self.mapping)
        else:
            embed = self.get_base_cog_embed(ctx, cog)

            paginator = Paginator(
                ctx,
                FieldBasedFormatter(embed, self.get_command_fields(ctx, cog), page_in_footer=True),
                center_button=self.view._center_button,
                other_components=[CogSelect(self.mapping, default=cog)],
                row=1,
            )

        await paginator.start(edit=True, interaction=interaction)


class CenterButton(discord.ui.Button[PaginatorView]):
    def __init__(self, ctx: Context) -> None:
        self.ctx: Context = ctx
        self._active: bool = False

        self._old_embed_store: discord.Embed | None = None
        self._old_button_store: dict[discord.ui.Button, bool] = {}

        super().__init__(emoji='\u2139', style=discord.ButtonStyle.primary)

    @staticmethod
    def get_embed(ctx: Context) -> discord.Embed:
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Help: {ctx.author.name}', icon_url=ctx.author.avatar.url)

        for name, value in (
            ('<argument>', 'This is a required argument.'),
            ('[argument]', 'This is an optional argument.'),
            ('<A | B>', 'This means that this argument can either be A or B.'),
            ('[A | B]', 'Similar to the previous one, but the argument is optional.'),
            ('<\'argument\'>', 'This argument should be typed exactly as shown.'),
            ('<argument...>', 'You can use this argument multiple times.'),
            ('<argument=X>', 'This means that X is the default value for the argument if none is passed.')
        ):
            embed.add_field(name=f'`{name}`', value=value, inline=False)

        return embed

    async def callback(self, interaction: discord.Interaction) -> None:
        self._active = not self._active

        if self._active:
            self._old_embed_store = interaction.message.embeds

            for button in self.view.children:
                if isinstance(button, CenterButton) or not isinstance(button, discord.ui.Button):
                    continue

                self._old_button_store[button] = button.disabled
                button.disabled = True

            await interaction.response.edit_message(embed=self.get_embed(self.ctx), view=self.view)

        else:
            for button in self.view.children:
                if not isinstance(button, discord.ui.Button):
                    continue

                button.disabled = self._old_button_store.get(button, False)

            await interaction.response.edit_message(embeds=self._old_embed_store, view=self.view)


class HelpCommand(commands.HelpCommand):
    """The bot's help command."""

    context: Context

    def __init__(self, **attrs: Any) -> None:
        super().__init__(command_attrs=dict(
            aliases=('h',),
            help='Shows help about the bot.',
        ), **attrs)

    @staticmethod
    def format_commands(cmds: list[Command]) -> str:
        return '\u2002'.join(f'`{cmd.qualified_name}`' for cmd in sorted(cmds, key=lambda c: c.qualified_name))

    def get_bot_mapping(self) -> dict[Cog, list[Command]]:
        mapping = super().get_bot_mapping()
        del mapping[None]

        return mapping

    @staticmethod
    def filter_mapping(mapping: dict[Cog, list[Command]]) -> dict[Cog, list[Command]]:
        return {cog: v for cog, v in mapping.items() if not getattr(cog, '__hidden__', True)}

    @staticmethod
    def commands_into_fields(ctx: Context, cmd: list[Command]) -> list[dict[str, str | bool]]:
        return [
            {
                'name': cutoff(ctx.clean_prefix + command.qualified_name + ' ' + command.signature, exact=True),
                'value': command.short_doc or command.description or 'No description provided.',
                'inline': False,
            }
            for command in sorted(cmd, key=lambda c: c.qualified_name)
            if not command.hidden
        ]

    @classmethod
    def get_bot_help_paginator(cls, ctx: Context, mapping: dict[Cog, list[Command]]) -> Paginator:
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Help: {ctx.author.name}', icon_url=ctx.author.avatar.url)

        mapping = cls.filter_mapping(mapping)
        fields = [
            {
                'name': f'{getattr(cog, "emoji", "")} {cog.qualified_name}',
                'value': f'{cog.description}\n{cls.format_commands(cmds)}',
                'inline': False,
            }
            for cog, cmds in mapping.items()
        ]

        return Paginator(
            ctx,
            FieldBasedFormatter(embed, fields, per_page=7, page_in_footer=True),
            center_button=CenterButton(ctx),
            other_components=[CogSelect(mapping)],
            row=1,
        )

    async def send_bot_help(self, mapping: dict[Cog | None, list[Command]]) -> None:
        """Send the bot's help command."""
        paginator = self.get_bot_help_paginator(self.context, mapping)
        await paginator.start(interaction=self.context.interaction if self.context.is_interaction else None)

    async def send_cog_help(self, cog: Cog) -> None:
        """Send the cog's help command."""
        embed = CogSelect.get_base_cog_embed(self.context, cog)
        fields = CogSelect.get_command_fields(self.context, cog)

        paginator = Paginator(
            self.context,
            FieldBasedFormatter(embed, fields, page_in_footer=True),
            center_button=CenterButton(ctx=self.context),
            other_components=[CogSelect(self.filter_mapping(self.get_bot_mapping()))],
            row=1,
        )

        await paginator.start(interaction=self.context.interaction if self.context.is_interaction else None)

    def get_base_command_embed(self, command: Command) -> discord.Embed:
        ctx: Context = self.context

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Help: {ctx.author.name}', icon_url=ctx.author.avatar.url)
        if emoji := getattr(ctx.cog, 'emoji', None):
            embed.set_thumbnail(url=image_url_from_emoji(emoji))

        body = command.help or 'No description provided.'

        signature = AnsiStringBuilder()
        signature.append(ctx.clean_prefix, color=AnsiColor.white, bold=True)
        signature.append(command.qualified_name + ' ', color=AnsiColor.green, bold=True)
        signature.extend(Command.ansi_signature_of(command))

        signature = signature.ensure_codeblock(fallback='md').dynamic(ctx)
        embed.description = f'{signature}\n{body.replace("{PREFIX}", ctx.clean_prefix)}'

        if command.aliases:
            embed.add_field(name='Aliases', value='\u2002'.join(f'`{alias}`' for alias in command.aliases))

        if cooldown := getattr(command.callback, '__database_cooldown__', None):
            humanized = humanize_duration(cooldown)

        elif cooldown := command._buckets._cooldown:
            humanized = pluralize(f'{cooldown.rate} time(s) per {humanize_duration(cooldown.per)}')

        else:
            humanized = None

        if humanized is not None:
            embed.add_field(name='Cooldown', value=humanized)

        if isinstance(command, Command):
            spec = command.permission_spec
            parts = []

            if user := spec.user:
                parts.append('User: ' + ', '.join(map(spec.permission_as_str, user)))

            if bot := spec.bot:
                parts.append('Bot: ' + ', '.join(map(spec.permission_as_str, bot)))

            embed.add_field(name='Required Permissions', value='\n'.join(parts), inline=False)

        return embed

    async def send_group_help(self, group: GroupCommand) -> None:
        """Send the group's help command."""
        embed = self.get_base_command_embed(group)
        fields = self.commands_into_fields(self.context, list(group.commands))

        paginator = Paginator(
            self.context,
            FieldBasedFormatter(embed, fields, page_in_footer=True),
            center_button=CenterButton(ctx=self.context),
        )

        await paginator.start(interaction=self.context.interaction if self.context.is_interaction else None)

    async def send_command_help(self, command: Command) -> None:
        """Send the command's help command."""
        embed = self.get_base_command_embed(command)

        view = UserView(self.context.author)
        view.add_item(CenterButton(ctx=self.context))

        if self.context.is_interaction:
            return await self.context.interaction.response.send_message(embed=embed, view=view)

        await self.context.send(embed=embed, view=view)

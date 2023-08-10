from os import getenv as env
from platform import system
from typing import Collection

import discord
from discord import AllowedMentions
from dotenv import load_dotenv

load_dotenv()

__all__ = (
    'beta',
    'name',
    'version',
    'description',
    'owner',
    'default_prefix',
    'allowed_mentions',
    'Colors',
    'DatabaseConfig',
    'Emojis',
    'token',
)

beta: bool = system() != 'Linux'

name: str = 'Coined'
version: str = '0.0.0'
description: str = (
    'Have fun with your friends with Coined, a carefully crafted, feature-packed, and open-source economy bot.'
)

# An ID or list of IDs
owner: Collection[int] | int = 414556245178056706
default_prefix: Collection[str] | str = '.' + '.' * beta
token: str = env('DISCORD_TOKEN')

ipc_secret = env('IPC_SECRET')
dbl_token: str = env('DBL_TOKEN')
dbl_secret: str = env('DBL_SECRET')

allowed_mentions: AllowedMentions = AllowedMentions.none()
allowed_mentions.users = True

multiplier_guilds: set[int] = {
    635944376761057282,  # CAIF
    893991611262976091,  # Unnamed bot testing
}

backups_channel: int = 1138551276062396469
guilds_channel: int = 1138551294907387974


class _RandomColor:
    def __get__(self, *_) -> int:
        return discord.Color.random().value


class Colors:
    primary: int = _RandomColor()  # 0x6199f2
    secondary: int = 0x6199f2
    success: int = 0x17ff70
    warning: int = 0xfcba03
    error: int = 0xff1759


class DatabaseConfig:
    name: str = 'dank_ripoff_remastered'
    user: str | None = None if beta else 'postgres'
    host: str | None = 'localhost'
    port: int | None = None
    password: str | None = None if beta else env('DATABASE_PASSWORD')


class Emojis:
    coin = '<:_:896432147152400394>'
    loading = '<a:_:825862907626913842>'
    space = '<:_:940748421701185637>'
    arrow = '<:_:831333449562062908>'
    bolt = '<:_:1137588508228321413>'
    max_bolt = '<:_:1138247288259616840>'

    enabled = '<:_:939549340458954762>'
    disabled = '<:_:939549360570662952>'
    neutral = '<:_:838593591965384734>'

    class Arrows:
        left: str = ''
        right: str = ''
        up: str = ''
        down: str = ''

        # Pagination
        previous: str = '\u25c0'
        forward: str = '\u25b6'
        first: str = '\u23ea'
        last: str = '\u23e9'

    dice = (
        ...,  # Index 0 is nothing
        '<:_:935227885671837747>',
        '<:_:935227904068034560>',
        '<:_:935227914163724398>',
        '<:_:935227927665188995>',
        '<:_:935227982426038292>',
        '<:_:935228010062282752>',
    )

    prestige = (
        '',  # Index 0 is nothing
        '<:prestige1:722846087622164560>',
        '<:prestige2:722846800175956080>',
        '<:prestige3:722847524427137125>',
        '<:prestige4:722848108501008436>',
        '<:prestige5:722849078794387547>',
        '<:prestige6:722850899692880013>',
        '<:prestige7:722853221755781200>',
        '<:prestige8:722853234888146944>',
        '<:prestige9:722853244606480505>',
        '<:prestige10:722853256048541696>',
        '<:prestige11:722961247305334874>',
        '<:prestige12:722961494660218900>',
        '<:prestige13:722966966603612201>',
        '<:prestige14:722966985700409405>',
        '<:prestige15:722966989307641876>',
        '<:prestige16:723205927188299787>',
        '<:prestige17:723205942635659325>',
        '<:prestige18:723205964270141440>',
        '<:prestige19:723205981927899266>',
        '<:prestige20:723205998625423371>',
    )

    @classmethod
    def get_prestige_emoji(cls, prestige: int, *, trailing_ws: bool = False) -> str:
        base = cls.prestige[prestige] if prestige < len(cls.prestige) else cls.prestige[-1]
        return base and f'{base} ' if trailing_ws else base

    class ProgressBars:
        left_empty = '<:_:937082616333602836>'
        left_low = '<:_:937082634046173194>'
        left_mid = '<:_:937082669068595300>'
        left_high = '<:_:937082728376045598>'
        left_full = '<:_:937082777927561297>'

        mid_empty = '<:_:937082833107828786>'
        mid_low = '<:_:937082868226752552>'
        mid_mid = '<:_:937082902880083988>'
        mid_high = '<:_:937082944655351860>'
        mid_full = '<:_:937082993057595473>'

        right_empty = '<:_:937083054340595803>'
        right_low = '<:_:937083097969754193>'
        right_mid = '<:_:937083245173026887>'
        right_high = '<:_:937083276827439164>'
        right_full = '<:_:937083328648056862>'


    class Expansion:
        first = '<:_:968651020097945811>'
        mid = '<:_:968652421721120828>'
        last = '<:_:968652421700124723>'
        ext = '<:_:968653920106872842>'
        single = standalone = '<:_:968652421377167371>'

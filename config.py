from os import getenv as env
from platform import system
from typing import Collection

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

name: str = 'Dank Ripoff Remastered'
version: str = '0.0.0'
description: str = 'Hmm...'

# An ID or list of IDs
owner: Collection[int] | int = 414556245178056706
default_prefix: Collection[str] | str = '.' + '.' * beta
token: str = env('DISCORD_TOKEN')

allowed_mentions: AllowedMentions = AllowedMentions.none()
allowed_mentions.users = True


class Colors:
    primary: int = 0x6199f2
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

    enabled = '<:_:939549340458954762>'
    disabled = '<:_:939549360570662952>'

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

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
    coin = '<:coin:896432147152400394>'
    loading = '<a:loading:825862907626913842>'
    space = '<:space:940748421701185637>'

    enabled = '<:enabled:939549340458954762>'
    disabled = '<:disabled:939549360570662952>'

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
        '<:dice_1:935227885671837747>',
        '<:dice_2:935227904068034560>',
        '<:dice_3:935227914163724398>',
        '<:dice_4:935227927665188995>',
        '<:dice_5:935227982426038292>',
        '<:dice_6:935228010062282752>',
    )

    class ProgressBars:
        left_empty = '<:pb_left_0:937082616333602836>'
        left_low = '<:pb_left_1:937082634046173194>'
        left_mid = '<:pb_left_2:937082669068595300>'
        left_high = '<:pb_left_3:937082728376045598>'
        left_full = '<:pb_left_4:937082777927561297>'

        mid_empty = '<:pb_mid_0:937082833107828786>'
        mid_low = '<:pb_mid_1:937082868226752552>'
        mid_mid = '<:pb_mid_2:937082902880083988>'
        mid_high = '<:pb_mid_3:937082944655351860>'
        mid_full = '<:pb_mid_4:937082993057595473>'

        right_empty = '<:pb_right_0:937083054340595803>'
        right_low = '<:pb_right_1:937083097969754193>'
        right_mid = '<:pb_right_2:937083245173026887>'
        right_high = '<:pb_right_3:937083276827439164>'
        right_full = '<:pb_right_4:937083328648056862>'

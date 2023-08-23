from app.core import Cog


class Combat(Cog):
    """Commands related to training and participating in combat and battling."""

    emoji = '\u2694\ufe0f'
    __hidden__ = True  # TODO Remove


setup = Combat.simple_setup

from typing import NamedTuple

from app.data.items import Item, Items


class Recipe(NamedTuple):
    key: str
    name: str
    description: str

    price: int
    ingredients: dict[Item, int]
    result: dict[Item, int]


class Recipes:
    durable_shovel = Recipe(
        key="durable_shovel",
        name="Durable Shovel",
        description=Items.durable_shovel.description,
        price=10_000,
        ingredients={
            Items.shovel: 3,
            Items.iron: 3,
        },
        result={
            Items.durable_shovel: 1,
        },
    )

    durable_pickaxe = Recipe(
        key="durable_pickaxe",
        name="Durable Pickaxe",
        description=Items.durable_pickaxe.description,
        price=10_000,
        ingredients={
            Items.pickaxe: 3,
            Items.iron: 3,
        },
        result={
            Items.durable_pickaxe: 1,
        },
    )

    diamond_pickaxe = Recipe(
        key="diamond_pickaxe",
        name="Diamond Pickaxe",
        description=Items.diamond_pickaxe.description,
        price=100_000,
        ingredients={
            Items.pickaxe: 3,
            Items.diamond: 3,
        },
        result={
            Items.diamond_pickaxe: 1,
        },
    )

    fish_bait = Recipe(
        key="fish_bait",
        name="Fish Bait",
        description=Items.fish_bait.description,
        price=50,
        ingredients={
            Items.worm: 3,
        },
        result={
            Items.fish_bait: 1,
        },
    )

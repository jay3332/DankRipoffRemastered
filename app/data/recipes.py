from typing import NamedTuple

from app.data.items import Item, Items


class Recipe(NamedTuple):
    key: str
    name: str
    emoji: str
    description: str

    price: int
    ingredients: dict[Item, int]
    result: dict[Item, int]

    def __hash__(self) -> int:
        return hash(self.key)


class Recipes:
    durable_shovel = Recipe(
        key="durable_shovel",
        name="Durable Shovel",
        description=Items.durable_shovel.description,
        emoji=Items.durable_shovel.emoji,
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
        emoji=Items.durable_pickaxe.emoji,
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
        emoji=Items.diamond_pickaxe.emoji,
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
        emoji=Items.fish_bait.emoji,
        price=50,
        ingredients={
            Items.worm: 3,
        },
        result={
            Items.fish_bait: 1,
        },
    )

    stick = Recipe(
        key="stick",
        name="Stick",
        description=Items.stick.description,
        emoji=Items.stick.emoji,
        price=10,
        ingredients={
            Items.wood: 2,
        },
        result={
            Items.stick: 1,
        },
    )

    sheet_of_paper = Recipe(
        key="sheet_of_paper",
        name="Sheet of Paper",
        description=Items.sheet_of_paper.description,
        emoji=Items.sheet_of_paper.emoji,
        price=5000,
        ingredients={
            Items.wood: 2,
            Items.banknote: 1,
        },
        result={
            Items.sheet_of_paper: 1,
        },
    )

    cigarette = Recipe(
        key="cigarette",
        name="Cigarette",
        description=Items.cigarette.description,
        emoji=Items.cigarette.emoji,
        price=7000,
        ingredients={
            Items.tobacco: 2,
            Items.cotton_ball: 2,
            Items.sheet_of_paper: 1,
        },
        result={
            Items.cigarette: 1,
        },
    )

    flour = Recipe(
        key="flour",
        name="Flour",
        description=Items.flour.description,
        emoji=Items.flour.emoji,
        price=150,
        ingredients={
            Items.wheat: 2,
        },
        result={
            Items.flour: 1,
        },
    )

    bread = Recipe(
        key="bread",
        name="Bread",
        description=Items.bread.description,
        emoji=Items.bread.emoji,
        price=200,
        ingredients={
            Items.flour: 2,
            Items.glass_of_water: 1,
        },
        result={
            Items.bread: 1,
        },
    )

    glass_of_water = Recipe(
        key="glass_of_water",
        name="Glass of Water",
        description=Items.glass_of_water.description,
        emoji=Items.glass_of_water.emoji,
        price=150,
        ingredients={
            Items.watering_can: 1,
            Items.cup: 1,
        },
        result={
            Items.glass_of_water: 1,
        },
    )

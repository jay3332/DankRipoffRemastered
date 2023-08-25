ALTER TABLE users
    ADD COLUMN IF NOT EXISTS orbs BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS battle_hp INTEGER DEFAULT 100,
    ADD COLUMN IF NOT EXISTS battle_stamina INTEGER DEFAULT 100,
    ADD COLUMN IF NOT EXISTS defeated_enemies TEXT[] NOT NULL DEFAULT '{}'::TEXT[];

CREATE TABLE IF NOT EXISTS abilities (
    user_id BIGINT NOT NULL,
    ability TEXT NOT NULL,
    exp BIGINT NOT NULL DEFAULT 0,
    equipped BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (user_id, ability)
);
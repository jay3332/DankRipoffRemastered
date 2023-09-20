CREATE TABLE IF NOT EXISTS fish_tanks (
    user_id BIGINT NOT NULL PRIMARY KEY,
    
);

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS fish_tank_max_coins BIGINT,
    ADD COLUMN IF NOT EXISTS fish_tank_max_capacity BIGINT;
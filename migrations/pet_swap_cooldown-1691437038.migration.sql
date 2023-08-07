ALTER TABLE users
    ADD COLUMN IF NOT EXISTS pet_operations INTEGER NOT NULL DEFAULT 0, -- divide by 2 to get pet_swaps
    ADD COLUMN IF NOT EXISTS pet_operations_cooldown_start TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
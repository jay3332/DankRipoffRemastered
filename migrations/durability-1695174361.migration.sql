ALTER TABLE items
    ADD COLUMN IF NOT EXISTS damage INTEGER DEFAULT NULL;
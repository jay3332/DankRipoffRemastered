ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_dbl_vote TIMESTAMP WITH TIME ZONE;

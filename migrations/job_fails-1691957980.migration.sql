ALTER TABLE users
    ADD COLUMN IF NOT EXISTS job_fails INTEGER;
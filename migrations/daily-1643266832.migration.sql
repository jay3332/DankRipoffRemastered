ALTER TABLE users
ADD COLUMN daily_streak INTEGER NOT NULL DEFAULT 0;

CREATE TABLE cooldowns (
    user_id BIGINT NOT NULL,
    command TEXT NOT NULL,
    expires TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (user_id, command)
);

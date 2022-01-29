
CREATE TABLE skills (
    user_id BIGINT NOT NULL,
    skill TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    on_cooldown_until TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (user_id, skill)
);


CREATE TABLE items (
    user_id BIGINT NOT NULL,
    item TEXT NOT NULL,
    count BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, item)
)

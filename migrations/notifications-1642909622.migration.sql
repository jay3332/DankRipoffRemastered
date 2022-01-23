
CREATE TABLE notifications (
    user_id BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL
)
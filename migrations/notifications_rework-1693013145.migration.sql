DROP TABLE IF EXISTS notifications;

CREATE TABLE notifications (
    user_id BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    type SMALLINT,
    data JSONB
);
CREATE TABLE IF NOT EXISTS guild_count_graph_data (
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    guild_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_coins_graph_data (
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    wallet INTEGER NOT NULL,
    total INTEGER NOT NULL
);

CREATE OR REPLACE FUNCTION expire_graph_data() RETURNS TRIGGER
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM user_coins_graph_data WHERE CURRENT_TIMESTAMP - timestamp > INTERVAL '2w'; -- 2 weeks
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER expire_graph_data_trigger
    AFTER INSERT ON user_coins_graph_data
    EXECUTE PROCEDURE expire_graph_data();
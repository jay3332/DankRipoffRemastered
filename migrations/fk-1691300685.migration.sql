ALTER TABLE items
    ADD FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE notifications
    ADD FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE skills
    ADD FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE crops
    ADD FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

ALTER TABLE cooldowns
    ADD FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;
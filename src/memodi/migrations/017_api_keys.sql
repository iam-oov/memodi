CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);

UPDATE users SET email = lower(email) WHERE email <> lower(email);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'api_key_hash'
    ) THEN
        INSERT INTO api_keys (user_id, key_hash)
        SELECT id, api_key_hash FROM users;

        ALTER TABLE users DROP COLUMN api_key_hash;
    END IF;
END $$;

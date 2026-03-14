-- Snippets Backend PostgreSQL Schema

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_types (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS source_publishers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    user_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS sources (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source_type_id INTEGER REFERENCES source_types(id),
    year TEXT,
    url TEXT,
    accessed_date TEXT,
    edition TEXT,
    pages TEXT,
    extra_notes TEXT,
    publisher_id INTEGER REFERENCES source_publishers(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_authors (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    first_name TEXT,
    last_name TEXT,
    author_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notes (
    id SERIAL PRIMARY KEY,
    body TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    locator_type TEXT,
    locator_value TEXT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    UNIQUE (name, user_id)
);

CREATE TABLE IF NOT EXISTS note_tags (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);
CREATE INDEX IF NOT EXISTS idx_notes_source_id ON notes(source_id);
CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes(user_id);
CREATE INDEX IF NOT EXISTS idx_sources_name ON sources(name);
CREATE INDEX IF NOT EXISTS idx_sources_user_id ON sources(user_id);
CREATE INDEX IF NOT EXISTS idx_note_tags_note_id ON note_tags(note_id);
CREATE INDEX IF NOT EXISTS idx_note_tags_tag_id ON note_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_source_authors_source_order ON source_authors(source_id, author_order);
CREATE INDEX IF NOT EXISTS idx_source_publishers_user_id ON source_publishers(user_id);
CREATE INDEX IF NOT EXISTS idx_tags_user_id ON tags(user_id);

-- Seed source types
INSERT INTO source_types (name) VALUES ('Book') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('Article') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('Magazine') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('YouTube Video') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('Other') ON CONFLICT DO NOTHING;

-- Migration: add user_id columns to existing tables if they don't have them
ALTER TABLE notes ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE sources ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE source_publishers ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE tags ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);

-- Drop the old unique constraint on tags.name and add (name, user_id) if needed
-- (handled idempotently: the CREATE TABLE above uses the new UNIQUE)

-- Set schema version (v2)
INSERT INTO schema_version (version) VALUES (2) ON CONFLICT (version) DO NOTHING;

-- === Migration v3: enforce NOT NULL on user_id, add ON DELETE CASCADE ===
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 3) THEN
        -- Enforce NOT NULL on user_id columns
        ALTER TABLE notes ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE sources ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE source_publishers ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE tags ALTER COLUMN user_id SET NOT NULL;

        -- Drop and re-add source_authors.source_id FK with CASCADE
        ALTER TABLE source_authors DROP CONSTRAINT IF EXISTS source_authors_source_id_fkey;
        ALTER TABLE source_authors ADD CONSTRAINT source_authors_source_id_fkey
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE;

        -- Drop and re-add note_tags FKs with CASCADE
        ALTER TABLE note_tags DROP CONSTRAINT IF EXISTS note_tags_note_id_fkey;
        ALTER TABLE note_tags ADD CONSTRAINT note_tags_note_id_fkey
            FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE;

        ALTER TABLE note_tags DROP CONSTRAINT IF EXISTS note_tags_tag_id_fkey;
        ALTER TABLE note_tags ADD CONSTRAINT note_tags_tag_id_fkey
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE;

        INSERT INTO schema_version (version) VALUES (3);
    END IF;
END $$;

-- === Migration v4: revoked_tokens table for JWT logout ===
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti TEXT PRIMARY KEY,
    revoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 4) THEN
        INSERT INTO schema_version (version) VALUES (4);
    END IF;
END $$;

-- === Migration v5: login_attempts table for brute-force protection ===
CREATE TABLE IF NOT EXISTS login_attempts (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_username_time ON login_attempts(username, attempted_at);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 5) THEN
        INSERT INTO schema_version (version) VALUES (5);
    END IF;
END $$;

-- === Migration v6: invite_codes table for registration gating ===
CREATE TABLE IF NOT EXISTS invite_codes (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    created_by INTEGER REFERENCES users(id),
    used_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 6) THEN
        INSERT INTO schema_version (version) VALUES (6);
    END IF;
END $$;

-- === Migration v7: updated_at column on notes ===
ALTER TABLE notes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 7) THEN
        INSERT INTO schema_version (version) VALUES (7);
    END IF;
END $$;

-- === Migration v8: email on users, magic_link_tokens, auth_providers ===
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;

-- Make password_hash nullable (magic-link-only users won't have one)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 8) THEN
        ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

        -- Partial unique index: only one account per verified email
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
            ON users (email) WHERE email IS NOT NULL;

        INSERT INTO schema_version (version) VALUES (8);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS magic_link_tokens (
    id SERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL,
    email TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_email ON magic_link_tokens(email);
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_hash ON magic_link_tokens(token_hash);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 9) THEN
        INSERT INTO schema_version (version) VALUES (9);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS auth_providers (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    linked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, provider_user_id)
);

CREATE INDEX IF NOT EXISTS idx_auth_providers_user_id ON auth_providers(user_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 10) THEN
        INSERT INTO schema_version (version) VALUES (10);
    END IF;
END $$;

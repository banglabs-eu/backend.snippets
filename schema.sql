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

-- === Migration v2: add user_id columns to existing tables ===
-- (The CREATE TABLE statements above already include user_id for fresh installs;
--  the ALTERs catch databases that pre-date multi-tenancy.)
ALTER TABLE notes ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE sources ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE source_publishers ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE tags ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);

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
-- (revoked_at column was removed in v12)
CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti TEXT PRIMARY KEY
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

-- === Migration v8: Google OAuth support ===
ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;

-- Make password_hash nullable for Google-only users
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 8) THEN
        ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
        INSERT INTO schema_version (version) VALUES (8);
    END IF;
END $$;

-- === Migration v9: magic links for passwordless sign-in ===
CREATE TABLE IF NOT EXISTS magic_links (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_magic_links_user_id ON magic_links(user_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 9) THEN
        INSERT INTO schema_version (version) VALUES (9);
    END IF;
END $$;

-- === Migration v10: location column on sources (e.g., lecture venues) ===
ALTER TABLE sources ADD COLUMN IF NOT EXISTS location TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 10) THEN
        INSERT INTO schema_version (version) VALUES (10);
    END IF;
END $$;

-- === Migration v11: date column on sources (e.g., when a lecture was given) ===
ALTER TABLE sources ADD COLUMN IF NOT EXISTS date TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 11) THEN
        INSERT INTO schema_version (version) VALUES (11);
    END IF;
END $$;

-- === Migration v12: drop unused revoked_at column from revoked_tokens ===
ALTER TABLE revoked_tokens DROP COLUMN IF EXISTS revoked_at;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 12) THEN
        INSERT INTO schema_version (version) VALUES (12);
    END IF;
END $$;

-- === Migration v13: posts (long-form text composed from multiple notes) ===
CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL DEFAULT '',
    published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS post_notes (
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, note_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_user_id ON posts(user_id);
CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published) WHERE published;
CREATE INDEX IF NOT EXISTS idx_post_notes_note_id ON post_notes(note_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 13) THEN
        INSERT INTO schema_version (version) VALUES (13);
    END IF;
END $$;

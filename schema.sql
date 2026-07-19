-- Snippets Backend PostgreSQL Schema

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- ===========================================================================
-- Migration v16: rename `notes` family to `snippets` family.
-- ===========================================================================
-- Frontend calls them snippets; the backend tables used to be called notes.
-- This block sits BEFORE the CREATE TABLE statements that use the new names so
-- a pre-v16 DB gets renamed before CREATE TABLE IF NOT EXISTS would otherwise
-- create empty husks that conflict with the rename.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'notes')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'snippets') THEN
        ALTER TABLE notes RENAME TO snippets;
        ALTER INDEX IF EXISTS idx_notes_created_at RENAME TO idx_snippets_created_at;
        ALTER INDEX IF EXISTS idx_notes_source_id  RENAME TO idx_snippets_source_id;
        ALTER INDEX IF EXISTS idx_notes_user_id    RENAME TO idx_snippets_user_id;
        ALTER INDEX IF EXISTS idx_notes_published  RENAME TO idx_snippets_published;
        ALTER SEQUENCE IF EXISTS notes_id_seq RENAME TO snippets_id_seq;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'note_tags')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'snippet_tags') THEN
        ALTER TABLE note_tags RENAME TO snippet_tags;
        ALTER TABLE snippet_tags RENAME COLUMN note_id TO snippet_id;
        ALTER INDEX IF EXISTS idx_note_tags_note_id RENAME TO idx_snippet_tags_snippet_id;
        ALTER INDEX IF EXISTS idx_note_tags_tag_id  RENAME TO idx_snippet_tags_tag_id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'post_notes')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'post_snippets') THEN
        ALTER TABLE post_notes RENAME TO post_snippets;
        ALTER TABLE post_snippets RENAME COLUMN note_id TO snippet_id;
        ALTER INDEX IF EXISTS idx_post_notes_note_id RENAME TO idx_post_snippets_snippet_id;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 16) THEN
        INSERT INTO schema_version (version) VALUES (16);
    END IF;
END $$;

-- ===========================================================================
-- Core tables (declarations use the post-v16 names everywhere).
-- ===========================================================================

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

CREATE TABLE IF NOT EXISTS snippets (
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

CREATE TABLE IF NOT EXISTS snippet_tags (
    snippet_id INTEGER NOT NULL REFERENCES snippets(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (snippet_id, tag_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_snippets_created_at ON snippets(created_at);
CREATE INDEX IF NOT EXISTS idx_snippets_source_id ON snippets(source_id);
CREATE INDEX IF NOT EXISTS idx_snippets_user_id ON snippets(user_id);
CREATE INDEX IF NOT EXISTS idx_sources_name ON sources(name);
CREATE INDEX IF NOT EXISTS idx_sources_user_id ON sources(user_id);
CREATE INDEX IF NOT EXISTS idx_snippet_tags_snippet_id ON snippet_tags(snippet_id);
CREATE INDEX IF NOT EXISTS idx_snippet_tags_tag_id ON snippet_tags(tag_id);
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
ALTER TABLE snippets ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE sources ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE source_publishers ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
ALTER TABLE tags ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);

INSERT INTO schema_version (version) VALUES (2) ON CONFLICT (version) DO NOTHING;

-- === Migration v3: enforce NOT NULL on user_id, add ON DELETE CASCADE ===
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 3) THEN
        -- Enforce NOT NULL on user_id columns
        ALTER TABLE snippets ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE sources ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE source_publishers ALTER COLUMN user_id SET NOT NULL;
        ALTER TABLE tags ALTER COLUMN user_id SET NOT NULL;

        -- Drop and re-add source_authors.source_id FK with CASCADE
        ALTER TABLE source_authors DROP CONSTRAINT IF EXISTS source_authors_source_id_fkey;
        ALTER TABLE source_authors ADD CONSTRAINT source_authors_source_id_fkey
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE;

        -- Drop and re-add snippet_tags FKs with CASCADE
        -- (constraint names retain "note_tags_*" on pre-v16 DBs that ran v3 before
        --  the rename; drop both possible names.)
        ALTER TABLE snippet_tags DROP CONSTRAINT IF EXISTS snippet_tags_snippet_id_fkey;
        ALTER TABLE snippet_tags DROP CONSTRAINT IF EXISTS note_tags_note_id_fkey;
        ALTER TABLE snippet_tags ADD CONSTRAINT snippet_tags_snippet_id_fkey
            FOREIGN KEY (snippet_id) REFERENCES snippets(id) ON DELETE CASCADE;

        ALTER TABLE snippet_tags DROP CONSTRAINT IF EXISTS snippet_tags_tag_id_fkey;
        ALTER TABLE snippet_tags DROP CONSTRAINT IF EXISTS note_tags_tag_id_fkey;
        ALTER TABLE snippet_tags ADD CONSTRAINT snippet_tags_tag_id_fkey
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

-- === Migration v7: updated_at column on snippets ===
ALTER TABLE snippets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;

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

-- === Migration v13: posts (long-form text composed from multiple snippets) ===
CREATE TABLE IF NOT EXISTS posts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL DEFAULT '',
    published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS post_snippets (
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    snippet_id INTEGER NOT NULL REFERENCES snippets(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, snippet_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_user_id ON posts(user_id);
CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published) WHERE published;
CREATE INDEX IF NOT EXISTS idx_post_snippets_snippet_id ON post_snippets(snippet_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 13) THEN
        INSERT INTO schema_version (version) VALUES (13);
    END IF;
END $$;

-- === Migration v14: posts.title + posts.slug for /post/<username>/<slug> URLs ===
ALTER TABLE posts ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS slug TEXT NOT NULL DEFAULT '';

-- Slug must be unique per user (drafts with empty slug excluded by partial index).
CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_user_slug
    ON posts(user_id, slug) WHERE slug <> '';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 14) THEN
        INSERT INTO schema_version (version) VALUES (14);
    END IF;
END $$;

-- === Migration v15: per-snippet publish + per-tag publish ===
-- A snippet becomes public independently of any post. When a snippet is public,
-- any source it points to is also publicly visible (derived via JOIN on read).
ALTER TABLE snippets ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE snippets ADD COLUMN IF NOT EXISTS published_at TIMESTAMP;
ALTER TABLE tags     ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_snippets_published ON snippets(published) WHERE published;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 15) THEN
        INSERT INTO schema_version (version) VALUES (15);
    END IF;
END $$;

-- === Migration v17: allow magic_links.user_id to be NULL ===
-- A row with user_id=NULL represents a "registration intent" — the email has
-- been verified but no account exists yet. Completing registration consumes
-- the link and creates the user. (v16 was the snippets/notes rename.)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 17) THEN
        ALTER TABLE magic_links ALTER COLUMN user_id DROP NOT NULL;
        INSERT INTO schema_version (version) VALUES (17);
    END IF;
END $$;

-- === Migration v18: SSO cutover to accounts.bang-labs.eu ===
-- Identity now lives in the shared accounts service (see
-- /home/adam/Bang-Labs/CLAUDE.md); /login and /register verify against it
-- instead of this table's own password_hash. Existing rows link to their
-- accounts identity by username on first post-cutover login (see
-- get_or_link_user_by_accounts in db.py) rather than a bulk backfill here.
ALTER TABLE users ADD COLUMN IF NOT EXISTS accounts_user_id INTEGER UNIQUE;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 18) THEN
        INSERT INTO schema_version (version) VALUES (18);
    END IF;
END $$;

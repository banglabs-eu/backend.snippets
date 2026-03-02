-- Snippets Backend PostgreSQL Schema

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS source_types (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS source_publishers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT
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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_authors (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS note_tags (
    note_id INTEGER NOT NULL REFERENCES notes(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (note_id, tag_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);
CREATE INDEX IF NOT EXISTS idx_notes_source_id ON notes(source_id);
CREATE INDEX IF NOT EXISTS idx_sources_name ON sources(name);
CREATE INDEX IF NOT EXISTS idx_note_tags_note_id ON note_tags(note_id);
CREATE INDEX IF NOT EXISTS idx_note_tags_tag_id ON note_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_source_authors_source_order ON source_authors(source_id, author_order);

-- Seed source types
INSERT INTO source_types (name) VALUES ('Book') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('Article') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('Magazine') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('YouTube Video') ON CONFLICT DO NOTHING;
INSERT INTO source_types (name) VALUES ('Other') ON CONFLICT DO NOTHING;

-- Set schema version
INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;

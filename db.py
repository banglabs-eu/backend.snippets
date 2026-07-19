"""Database access layer for Snippets backend."""

import secrets
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool


def init_db(database_url: str, minconn: int = 2, maxconn: int = 10):
    """Initialize DB: create connection pool and run schema."""
    pool = ThreadedConnectionPool(
        minconn, maxconn, database_url,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn = pool.getconn()
    try:
        schema_path = Path(__file__).parent / "schema.sql"
        with open(schema_path, "r") as f:
            sql = f.read()
        cur = conn.cursor()
        # Serialize schema setup across uvicorn workers — without this, parallel
        # workers race for AccessExclusiveLock on the same tables and deadlock.
        cur.execute("SELECT pg_advisory_lock(7340271)")
        try:
            cur.execute(sql)
            cur.execute("DELETE FROM login_attempts WHERE attempted_at < NOW() - INTERVAL '30 days'")
            conn.commit()
        finally:
            cur.execute("SELECT pg_advisory_unlock(7340271)")
            conn.commit()
    finally:
        pool.putconn(conn)
    return pool


# --- Users ---

def create_user(conn, username: str, password_hash: str) -> dict:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id, username, created_at",
        (username, password_hash),
    )
    row = cur.fetchone()
    conn.commit()
    return row


def get_user_by_username(conn, username: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    return cur.fetchone()


def get_user_by_id(conn, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cur.fetchone()


def get_user_by_google_id(conn, google_id: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE google_id = %s", (google_id,))
    return cur.fetchone()


def create_google_user(conn, username: str, google_id: str, email: str | None = None) -> dict:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash, google_id, email) VALUES (%s, NULL, %s, %s) RETURNING id, username, created_at",
        (username, google_id, email),
    )
    row = cur.fetchone()
    conn.commit()
    return row


def link_google_account(conn, user_id: int, google_id: str, email: str | None = None):
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET google_id = %s, email = COALESCE(%s, email) WHERE id = %s",
        (google_id, email, user_id),
    )
    conn.commit()


def delete_user(conn, user_id: int):
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()


# --- accounts.bang-labs.eu SSO linkage ---

def get_user_by_accounts_id(conn, accounts_user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE accounts_user_id = %s", (accounts_user_id,))
    return cur.fetchone()


def link_accounts_id(conn, user_id: int, accounts_user_id: int) -> dict:
    """Backfill: an existing pre-cutover row gets linked to the accounts
    identity of the same username, the first time that person logs in
    post-cutover."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET accounts_user_id = %s WHERE id = %s RETURNING *",
        (accounts_user_id, user_id),
    )
    row = cur.fetchone()
    conn.commit()
    return row


def create_user_from_accounts(conn, username: str, accounts_user_id: int) -> dict:
    """New signup: invite already validated, accounts identity already
    created — link the two. No local password; accounts owns that now."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, password_hash, accounts_user_id) VALUES (%s, NULL, %s) "
        "RETURNING id, username, created_at",
        (username, accounts_user_id),
    )
    row = cur.fetchone()
    conn.commit()
    return row


def update_user_password(conn, user_id: int, password_hash: str):
    cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
    conn.commit()


# --- Invite Codes ---

def create_invite_code(conn, created_by: int | None = None) -> str:
    code = secrets.token_urlsafe(16)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO invite_codes (code, created_by) VALUES (%s, %s)",
        (code, created_by),
    )
    conn.commit()
    return code


def is_invite_code_valid(conn, code: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM invite_codes WHERE code = %s AND used_by IS NULL", (code,))
    return cur.fetchone() is not None


def validate_and_use_invite_code(conn, code: str, user_id: int) -> bool:
    cur = conn.cursor()
    cur.execute(
        "UPDATE invite_codes SET used_by = %s, used_at = NOW() WHERE code = %s AND used_by IS NULL",
        (user_id, code),
    )
    conn.commit()
    return cur.rowcount == 1


def get_invite_codes(conn, created_by: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, code, used_by, created_at, used_at FROM invite_codes WHERE created_by = %s ORDER BY created_at DESC",
        (created_by,),
    )
    return cur.fetchall()


# --- Login Attempt Tracking ---

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def record_failed_login(conn, username: str):
    cur = conn.cursor()
    cur.execute("INSERT INTO login_attempts (username) VALUES (%s)", (username,))
    conn.commit()


def get_recent_failed_attempts(conn, username: str) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE username = %s AND attempted_at > NOW() - make_interval(mins => %s)",
        (username, LOCKOUT_MINUTES),
    )
    return cur.fetchone()["count"]


def clear_failed_attempts(conn, username: str):
    cur = conn.cursor()
    cur.execute("DELETE FROM login_attempts WHERE username = %s", (username,))
    conn.commit()


# --- Token Revocation ---

def revoke_token(conn, jti: str):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO revoked_tokens (jti) VALUES (%s) ON CONFLICT DO NOTHING",
        (jti,),
    )
    conn.commit()


def is_token_revoked(conn, jti: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM revoked_tokens WHERE jti = %s", (jti,))
    return cur.fetchone() is not None


# --- Notes ---

def create_snippet(conn, body: str, user_id: int, source_id: int | None = None,
                locator_type: str | None = None, locator_value: str | None = None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO snippets (body, source_id, locator_type, locator_value, user_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (body, source_id, locator_type, locator_value, user_id),
    )
    row = cur.fetchone()
    conn.commit()
    return row["id"]


def update_snippet_source(conn, snippet_id: int, source_id: int, user_id: int):
    cur = conn.cursor()
    cur.execute("UPDATE snippets SET source_id = %s WHERE id = %s AND user_id = %s", (source_id, snippet_id, user_id))
    conn.commit()


def update_snippet_body(conn, snippet_id: int, body: str, user_id: int):
    cur = conn.cursor()
    cur.execute(
        "UPDATE snippets SET body = %s, updated_at = NOW() WHERE id = %s AND user_id = %s",
        (body, snippet_id, user_id),
    )
    conn.commit()


def get_snippet(conn, snippet_id: int, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM snippets WHERE id = %s AND user_id = %s", (snippet_id, user_id))
    return cur.fetchone()


def search_snippets(conn, query: str, user_id: int, limit: int = 50) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM snippets WHERE body ILIKE %s AND user_id = %s ORDER BY created_at DESC LIMIT %s",
        (f"%{query}%", user_id, limit),
    )
    return cur.fetchall()


def get_all_snippets(conn, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM snippets WHERE user_id = %s ORDER BY created_at ASC", (user_id,))
    return cur.fetchall()


def get_snippets_by_source(conn, source_id: int, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM snippets WHERE source_id = %s AND user_id = %s ORDER BY created_at ASC",
        (source_id, user_id),
    )
    return cur.fetchall()


def get_snippets_by_tag(conn, tag_id: int, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT n.* FROM snippets n
           JOIN snippet_tags nt ON n.id = nt.snippet_id
           WHERE nt.tag_id = %s AND n.user_id = %s
           ORDER BY n.created_at ASC""",
        (tag_id, user_id),
    )
    return cur.fetchall()


def get_snippets_by_author(conn, author_id: int, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT n.* FROM snippets n
           JOIN sources s ON n.source_id = s.id
           JOIN source_authors sa ON sa.source_id = s.id
           WHERE sa.id = %s AND n.user_id = %s
           ORDER BY n.created_at ASC""",
        (author_id, user_id),
    )
    return cur.fetchall()


def get_sourceless_snippets(conn, snippet_ids: list[int], user_id: int) -> list[int]:
    if not snippet_ids:
        return []
    placeholders = ",".join("%s" for _ in snippet_ids)
    cur = conn.cursor()
    cur.execute(
        f"SELECT id FROM snippets WHERE id IN ({placeholders}) AND source_id IS NULL AND user_id = %s",
        snippet_ids + [user_id],
    )
    rows = cur.fetchall()
    return [r["id"] for r in rows]


def bulk_update_snippet_source(conn, snippet_ids: list[int], source_id: int, user_id: int):
    if not snippet_ids:
        return
    placeholders = ",".join("%s" for _ in snippet_ids)
    cur = conn.cursor()
    cur.execute(
        f"UPDATE snippets SET source_id = %s WHERE id IN ({placeholders}) AND user_id = %s",
        [source_id] + snippet_ids + [user_id],
    )
    conn.commit()


# --- Sources ---

def create_source(conn, name: str, user_id: int, source_type_id: int | None = None,
                  year: str | None = None, url: str | None = None,
                  accessed_date: str | None = None, edition: str | None = None,
                  pages: str | None = None, extra_notes: str | None = None,
                  publisher_id: int | None = None, location: str | None = None,
                  date: str | None = None) -> int:
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO sources (name, source_type_id, year, url, accessed_date, edition, pages, extra_notes, publisher_id, location, date, user_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (name, source_type_id, year, url, accessed_date, edition, pages, extra_notes, publisher_id, location, date, user_id),
    )
    row = cur.fetchone()
    conn.commit()
    return row["id"]


def get_source(conn, source_id: int, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM sources WHERE id = %s AND user_id = %s", (source_id, user_id))
    return cur.fetchone()


def search_sources(conn, prefix: str, user_id: int, limit: int = 20) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM sources WHERE name ILIKE %s AND user_id = %s ORDER BY name LIMIT %s",
        (f"{prefix}%", user_id, limit),
    )
    return cur.fetchall()


def get_recent_sources(conn, user_id: int, limit: int = 10) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT s.* FROM sources s
           LEFT JOIN snippets n ON n.source_id = s.id
           WHERE s.user_id = %s
           GROUP BY s.id
           ORDER BY MAX(COALESCE(n.created_at, s.created_at)) DESC
           LIMIT %s""",
        (user_id, limit),
    )
    return cur.fetchall()


def get_all_sources(conn, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM sources WHERE user_id = %s ORDER BY name", (user_id,))
    return cur.fetchall()


_SOURCE_UPDATABLE = {
    "name", "source_type_id", "year", "url", "accessed_date",
    "edition", "pages", "extra_notes", "publisher_id", "location", "date",
}


def update_source(conn, source_id: int, user_id: int, fields: dict) -> dict | None:
    fields = {k: v for k, v in fields.items() if k in _SOURCE_UPDATABLE}
    if not fields:
        return get_source(conn, source_id, user_id)
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [source_id, user_id]
    cur = conn.cursor()
    cur.execute(
        f"UPDATE sources SET {set_clause} WHERE id = %s AND user_id = %s RETURNING *",
        values,
    )
    row = cur.fetchone()
    conn.commit()
    return row


def get_sources_by_author(conn, author_last: str, author_first: str, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT s.* FROM sources s
           JOIN source_authors sa ON sa.source_id = s.id
           WHERE LOWER(sa.last_name) = LOWER(%s) AND LOWER(sa.first_name) = LOWER(%s)
             AND s.user_id = %s
           ORDER BY s.name""",
        (author_last, author_first, user_id),
    )
    return cur.fetchall()


# --- Source Types (shared, no user_id) ---

def get_source_types(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM source_types ORDER BY id")
    return cur.fetchall()


def get_source_type(conn, type_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM source_types WHERE id = %s", (type_id,))
    return cur.fetchone()


def create_source_type(conn, name: str) -> int:
    cur = conn.cursor()
    cur.execute("INSERT INTO source_types (name) VALUES (%s) RETURNING id", (name,))
    row = cur.fetchone()
    conn.commit()
    return row["id"]


def get_source_type_by_name(conn, name: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM source_types WHERE LOWER(name) = LOWER(%s)", (name.strip(),))
    return cur.fetchone()


def get_or_create_source_type_by_name(conn, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("source type name required")
    existing = get_source_type_by_name(conn, name)
    if existing:
        return existing["id"]
    return create_source_type(conn, name)


# --- Publishers ---

def find_publisher(conn, name: str, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM source_publishers WHERE LOWER(name) = LOWER(%s) AND user_id = %s",
        (name, user_id),
    )
    return cur.fetchone()


def get_publisher(conn, publisher_id: int, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM source_publishers WHERE id = %s AND user_id = %s", (publisher_id, user_id))
    return cur.fetchone()


def create_publisher(conn, name: str, user_id: int, city: str | None = None) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO source_publishers (name, city, user_id) VALUES (%s, %s, %s) RETURNING id",
        (name, city, user_id),
    )
    row = cur.fetchone()
    conn.commit()
    return row["id"]


def get_or_create_publisher(conn, name: str, user_id: int, city: str | None = None) -> int:
    existing = find_publisher(conn, name, user_id)
    if existing:
        return existing["id"]
    return create_publisher(conn, name, user_id, city)


def search_publishers(conn, prefix: str, user_id: int, limit: int = 20) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM source_publishers WHERE name ILIKE %s AND user_id = %s ORDER BY name LIMIT %s",
        (f"{prefix}%", user_id, limit),
    )
    return cur.fetchall()


def search_publisher_cities(conn, prefix: str, user_id: int, limit: int = 20) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT city FROM source_publishers
           WHERE city IS NOT NULL AND city ILIKE %s AND user_id = %s
           ORDER BY city LIMIT %s""",
        (f"{prefix}%", user_id, limit),
    )
    rows = cur.fetchall()
    return [r["city"] for r in rows]


def search_author_last_names(conn, prefix: str, user_id: int, limit: int = 20) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT sa.last_name FROM source_authors sa
           JOIN sources s ON sa.source_id = s.id
           WHERE sa.last_name ILIKE %s AND s.user_id = %s
           ORDER BY sa.last_name LIMIT %s""",
        (f"{prefix}%", user_id, limit),
    )
    rows = cur.fetchall()
    return [r["last_name"] for r in rows]


def search_author_first_names(conn, prefix: str, user_id: int, limit: int = 20) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT sa.first_name FROM source_authors sa
           JOIN sources s ON sa.source_id = s.id
           WHERE sa.first_name ILIKE %s AND s.user_id = %s
           ORDER BY sa.first_name LIMIT %s""",
        (f"{prefix}%", user_id, limit),
    )
    rows = cur.fetchall()
    return [r["first_name"] for r in rows]


# --- Authors ---

def add_author(conn, source_id: int, first_name: str,
               last_name: str, order: int) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO source_authors (source_id, first_name, last_name, author_order) VALUES (%s, %s, %s, %s) RETURNING id",
        (source_id, first_name, last_name, order),
    )
    row = cur.fetchone()
    conn.commit()
    return row["id"]


def get_authors_for_source(conn, source_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM source_authors WHERE source_id = %s ORDER BY author_order",
        (source_id,),
    )
    return cur.fetchall()


def get_all_authors(conn, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT sa.* FROM source_authors sa
           JOIN sources s ON sa.source_id = s.id
           WHERE s.user_id = %s
           ORDER BY sa.last_name, sa.first_name""",
        (user_id,),
    )
    return cur.fetchall()


def get_recent_authors(conn, user_id: int, limit: int = 10) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT sa.* FROM source_authors sa
           JOIN sources s ON sa.source_id = s.id
           WHERE s.user_id = %s
           GROUP BY sa.id, sa.source_id, sa.first_name, sa.last_name, sa.author_order
           ORDER BY MAX(s.created_at) DESC
           LIMIT %s""",
        (user_id, limit),
    )
    return cur.fetchall()


def delete_author(conn, author_id: int, user_id: int) -> bool:
    """Delete a source_author the user owns (via source ownership)."""
    cur = conn.cursor()
    cur.execute(
        """DELETE FROM source_authors
           WHERE id = %s
             AND source_id IN (SELECT id FROM sources WHERE user_id = %s)""",
        (author_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_author(conn, author_id: int, user_id: int) -> dict | None:
    """Return an author row only if their parent source belongs to the user."""
    cur = conn.cursor()
    cur.execute(
        """SELECT sa.* FROM source_authors sa
           JOIN sources s ON sa.source_id = s.id
           WHERE sa.id = %s AND s.user_id = %s""",
        (author_id, user_id),
    )
    return cur.fetchone()


def update_author(conn, author_id: int, user_id: int, first_name: str | None, last_name: str | None) -> dict | None:
    """Update first/last name on an author the user owns (via source ownership)."""
    fields: dict = {}
    if first_name is not None:
        fields["first_name"] = first_name
    if last_name is not None:
        fields["last_name"] = last_name
    if not fields:
        return get_author(conn, author_id, user_id)
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [author_id, user_id]
    cur = conn.cursor()
    cur.execute(
        f"""UPDATE source_authors SET {set_clause}
            WHERE id = %s
              AND source_id IN (SELECT id FROM sources WHERE user_id = %s)
            RETURNING *""",
        values,
    )
    row = cur.fetchone()
    conn.commit()
    return row


def search_authors(conn, prefix: str, user_id: int, limit: int = 20) -> list[dict]:
    p = f"{prefix}%"
    cur = conn.cursor()
    cur.execute(
        """SELECT sa.* FROM source_authors sa
           JOIN sources s ON sa.source_id = s.id
           WHERE (sa.last_name ILIKE %s OR sa.first_name ILIKE %s)
             AND s.user_id = %s
           ORDER BY sa.last_name, sa.first_name LIMIT %s""",
        (p, p, user_id, limit),
    )
    return cur.fetchall()


# --- Tags ---

def get_or_create_tag(conn, name: str, user_id: int) -> int:
    name = name.strip().lower()
    cur = conn.cursor()
    cur.execute("SELECT id FROM tags WHERE name = %s AND user_id = %s", (name, user_id))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute("INSERT INTO tags (name, user_id) VALUES (%s, %s) RETURNING id", (name, user_id))
    row = cur.fetchone()
    conn.commit()
    return row["id"]


def get_tag(conn, tag_id: int, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM tags WHERE id = %s AND user_id = %s", (tag_id, user_id))
    return cur.fetchone()


def get_tag_by_name(conn, name: str, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM tags WHERE name = %s AND user_id = %s", (name.strip().lower(), user_id))
    return cur.fetchone()


def search_tags(conn, prefix: str, user_id: int, limit: int = 20) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM tags WHERE name ILIKE %s AND user_id = %s ORDER BY name LIMIT %s",
        (f"{prefix.lower()}%", user_id, limit),
    )
    return cur.fetchall()


def get_all_tags(conn, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM tags WHERE user_id = %s ORDER BY name", (user_id,))
    return cur.fetchall()


def get_recent_tags(conn, user_id: int, limit: int = 10) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT t.* FROM tags t
           JOIN snippet_tags nt ON t.id = nt.tag_id
           JOIN snippets n ON n.id = nt.snippet_id
           WHERE n.user_id = %s
           GROUP BY t.id, t.name, t.user_id
           ORDER BY MAX(n.created_at) DESC
           LIMIT %s""",
        (user_id, limit),
    )
    return cur.fetchall()


def add_tag_to_snippet(conn, snippet_id: int, tag_id: int):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO snippet_tags (snippet_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (snippet_id, tag_id),
    )
    conn.commit()


def remove_tag_from_snippet(conn, snippet_id: int, tag_id: int):
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM snippet_tags WHERE snippet_id = %s AND tag_id = %s",
        (snippet_id, tag_id),
    )
    conn.commit()


def delete_snippet(conn, snippet_id: int, user_id: int):
    cur = conn.cursor()
    cur.execute("DELETE FROM snippet_tags WHERE snippet_id = %s", (snippet_id,))
    cur.execute("DELETE FROM snippets WHERE id = %s AND user_id = %s", (snippet_id, user_id))
    conn.commit()


def publish_snippet(conn, snippet_id: int, user_id: int, public_tag_ids: list[int]) -> bool:
    """Mark a snippet as published and mark the supplied tag_ids (must be owned by the user
    AND attached to this snippet) as public. Idempotent. Returns True if the snippet was found."""
    cur = conn.cursor()
    cur.execute(
        """UPDATE snippets
              SET published = TRUE,
                  published_at = COALESCE(published_at, NOW())
            WHERE id = %s AND user_id = %s""",
        (snippet_id, user_id),
    )
    if cur.rowcount == 0:
        conn.commit()
        return False
    if public_tag_ids:
        placeholders = ",".join("%s" for _ in public_tag_ids)
        cur.execute(
            f"""UPDATE tags SET published = TRUE
                  WHERE user_id = %s AND id IN ({placeholders})
                    AND id IN (SELECT tag_id FROM snippet_tags WHERE snippet_id = %s)""",
            [user_id, *public_tag_ids, snippet_id],
        )
    conn.commit()
    return True


def unpublish_snippet(conn, snippet_id: int, user_id: int) -> bool:
    """Mark a note as not-public. Tags stay published — they may be shared with other public snippets."""
    cur = conn.cursor()
    cur.execute(
        "UPDATE snippets SET published = FALSE WHERE id = %s AND user_id = %s",
        (snippet_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_public_snippets_by_username(conn, username: str) -> list[dict]:
    """Return published snippets owned by `username`, with source name/type/locator hints."""
    cur = conn.cursor()
    cur.execute(
        """SELECT n.id, n.body, n.source_id, n.locator_type, n.locator_value,
                  n.created_at, n.updated_at, n.published_at,
                  s.name AS source_name, st.name AS source_type
             FROM snippets n
             JOIN users u ON u.id = n.user_id
        LEFT JOIN sources s ON s.id = n.source_id
        LEFT JOIN source_types st ON st.id = s.source_type_id
            WHERE u.username = %s AND n.published = TRUE
            ORDER BY COALESCE(n.published_at, n.created_at) DESC""",
        (username,),
    )
    return cur.fetchall()


def _public_dashboard(conn, *, username: str | None) -> dict:
    """Shared core of `get_public_dashboard` (per-user) and
    `get_global_public_dashboard` (cross-user). When `username` is set, every
    query is filtered to that user via a JOIN on `users`; when None, the
    queries cover every user.

    Returns the dict shape consumed by the frontend Dashboard's data hook —
    snippets + tags + reachable sources/authors/publishers + the public
    snippet→tag map.
    """
    cur = conn.cursor()
    user_join = " JOIN users u ON u.id = sn.user_id" if username else ""
    user_where = " AND u.username = %s" if username else ""
    user_param: tuple = (username,) if username else ()
    snippet_limit = "" if username else " LIMIT 1000"

    cur.execute(
        f"""SELECT sn.id, sn.body, sn.source_id, sn.locator_type, sn.locator_value,
                   sn.user_id, sn.created_at, sn.updated_at, sn.published_at,
                   TRUE AS published
              FROM snippets sn{user_join}
             WHERE sn.published = TRUE{user_where}
             ORDER BY sn.created_at ASC{snippet_limit}""",
        user_param,
    )
    snippets = cur.fetchall()

    if username:
        cur.execute(
            """SELECT t.id, t.name, t.user_id, t.published
                 FROM tags t JOIN users u ON u.id = t.user_id
                WHERE u.username = %s AND t.published = TRUE
                ORDER BY t.name""",
            (username,),
        )
    else:
        cur.execute(
            """SELECT t.id, t.name, t.user_id, t.published
                 FROM tags t WHERE t.published = TRUE
                ORDER BY t.name""",
        )
    tags = cur.fetchall()

    cur.execute(
        f"""SELECT DISTINCT s.id, s.name, s.source_type_id, s.year, s.url,
                   s.accessed_date, s.edition, s.pages, s.extra_notes,
                   s.publisher_id, s.location, s.date, s.user_id, s.created_at
              FROM sources s
              JOIN snippets sn ON sn.source_id = s.id{user_join}
             WHERE sn.published = TRUE{user_where}
             ORDER BY s.name""",
        user_param,
    )
    sources = cur.fetchall()

    cur.execute(
        f"""SELECT DISTINCT sa.id, sa.source_id, sa.first_name, sa.last_name, sa.author_order
              FROM source_authors sa
              JOIN sources s ON s.id = sa.source_id
              JOIN snippets sn ON sn.source_id = s.id{user_join}
             WHERE sn.published = TRUE{user_where}
             ORDER BY sa.last_name, sa.first_name""",
        user_param,
    )
    authors = cur.fetchall()

    cur.execute(
        f"""SELECT DISTINCT p.id, p.name, p.city, p.user_id
              FROM source_publishers p
              JOIN sources s ON s.publisher_id = p.id
              JOIN snippets sn ON sn.source_id = s.id{user_join}
             WHERE sn.published = TRUE{user_where}
             ORDER BY p.name""",
        user_param,
    )
    publishers = cur.fetchall()

    cur.execute("SELECT id, name FROM source_types ORDER BY name")
    source_types = cur.fetchall()

    cur.execute(
        f"""SELECT st.snippet_id, t.id, t.name, t.user_id, t.published
              FROM tags t
              JOIN snippet_tags st ON st.tag_id = t.id
              JOIN snippets sn ON sn.id = st.snippet_id{user_join}
             WHERE sn.published = TRUE AND t.published = TRUE{user_where}""",
        user_param,
    )
    snippet_tags: dict[int, list[dict]] = {}
    for r in cur.fetchall():
        snippet_tags.setdefault(r.pop("snippet_id"), []).append(r)

    return {
        "snippets": snippets,
        "tags": tags,
        "sources": sources,
        "authors": authors,
        "publishers": publishers,
        "source_types": source_types,
        "snippet_tags": snippet_tags,
    }


def get_global_public_dashboard(conn) -> dict:
    """Aggregated dataset for the global Dashboard view at /public.
    Returns every user's public snippets/tags plus the sources/authors/publishers
    reachable from those snippets. Tag-name collisions across users are kept as
    separate entities (tag.id is unique)."""
    return _public_dashboard(conn, username=None)


def get_public_dashboard(conn, username: str) -> dict:
    """Aggregated dataset for one user's public Dashboard."""
    return _public_dashboard(conn, username=username)


def get_public_snippet(conn, username: str, snippet_id: int) -> dict | None:
    """Single published note for a username, with source/locator hints. None unless published=TRUE."""
    cur = conn.cursor()
    cur.execute(
        """SELECT n.id, n.body, n.source_id, n.locator_type, n.locator_value,
                  n.created_at, n.updated_at, n.published_at,
                  u.username,
                  s.name AS source_name, st.name AS source_type
             FROM snippets n
             JOIN users u ON u.id = n.user_id
        LEFT JOIN sources s ON s.id = n.source_id
        LEFT JOIN source_types st ON st.id = s.source_type_id
            WHERE u.username = %s AND n.id = %s AND n.published = TRUE""",
        (username, snippet_id),
    )
    return cur.fetchone()


def get_public_tags_for_snippets(conn, snippet_ids: list[int]) -> dict[int, list[dict]]:
    """For a batch of (already-public) snippets, return only the tags that are themselves marked public."""
    if not snippet_ids:
        return {}
    placeholders = ",".join("%s" for _ in snippet_ids)
    cur = conn.cursor()
    cur.execute(
        f"""SELECT nt.snippet_id, t.id, t.name
              FROM tags t
              JOIN snippet_tags nt ON t.id = nt.tag_id
             WHERE nt.snippet_id IN ({placeholders}) AND t.published = TRUE
             ORDER BY t.name""",
        snippet_ids,
    )
    out: dict[int, list[dict]] = {nid: [] for nid in snippet_ids}
    for row in cur.fetchall():
        out[row["snippet_id"]].append({"id": row["id"], "name": row["name"]})
    return out


def get_tags_for_snippet(conn, snippet_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT t.* FROM tags t
           JOIN snippet_tags nt ON t.id = nt.tag_id
           WHERE nt.snippet_id = %s
           ORDER BY t.name""",
        (snippet_id,),
    )
    return cur.fetchall()


def delete_tag(conn, tag_id: int, user_id: int) -> bool:
    """Delete a tag. snippet_tags rows cascade via FK."""
    cur = conn.cursor()
    cur.execute("DELETE FROM tags WHERE id = %s AND user_id = %s", (tag_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def get_tags_for_snippets(conn, snippet_ids: list[int], user_id: int) -> dict[int, list[dict]]:
    """Return {snippet_id: [tag_rows]} for a batch of note ids."""
    if not snippet_ids:
        return {}
    placeholders = ",".join("%s" for _ in snippet_ids)
    cur = conn.cursor()
    cur.execute(
        f"""SELECT nt.snippet_id, t.* FROM tags t
            JOIN snippet_tags nt ON t.id = nt.tag_id
            JOIN snippets n ON n.id = nt.snippet_id
            WHERE nt.snippet_id IN ({placeholders}) AND n.user_id = %s
            ORDER BY t.name""",
        snippet_ids + [user_id],
    )
    rows = cur.fetchall()
    result: dict[int, list] = {nid: [] for nid in snippet_ids}
    for r in rows:
        result[r["snippet_id"]].append(r)
    return result


# --- Citation ---

def build_citation(conn, source_id: int, user_id: int) -> str:
    """Build an MLA-ish citation string for a source."""
    src = get_source(conn, source_id, user_id)
    if not src:
        return ""
    parts = []
    authors = get_authors_for_source(conn, source_id)
    if authors:
        author_strs = []
        for a in authors:
            if a["last_name"] and a["first_name"]:
                author_strs.append(f'{a["last_name"]}, {a["first_name"]}')
            elif a["last_name"]:
                author_strs.append(a["last_name"])
            elif a["first_name"]:
                author_strs.append(a["first_name"])
        if len(author_strs) == 1:
            parts.append(author_strs[0] + ".")
        elif len(author_strs) == 2:
            parts.append(f"{author_strs[0]}, and {author_strs[1]}.")
        elif len(author_strs) > 2:
            parts.append(f"{author_strs[0]}, et al.")

    parts.append(f'*{src["name"]}*.')

    if src["source_type_id"]:
        st = get_source_type(conn, src["source_type_id"])
        if st:
            parts.append(st["name"] + ".")

    if src["edition"]:
        parts.append(f'{src["edition"]} ed.')

    if src["publisher_id"]:
        pub = get_publisher(conn, src["publisher_id"], user_id)
        if pub:
            pub_str = pub["name"]
            if pub["city"]:
                pub_str = f'{pub["city"]}: {pub_str}'
            parts.append(pub_str + ",")

    if src["year"]:
        parts.append(f'{src["year"]}.')

    if src["pages"]:
        parts.append(f'pp. {src["pages"]}.')

    if src["url"]:
        parts.append(src["url"] + ".")

    if src["accessed_date"]:
        parts.append(f'Accessed {src["accessed_date"]}.')

    return " ".join(parts)


# --- Magic Links ---

def create_magic_link(conn, user_id: int | None, email: str, token: str, expires_at) -> None:
    """Insert a magic link. `user_id=None` is a registration intent — the email
    is verified but no account exists yet; completing registration creates one."""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO magic_links (token, user_id, email, expires_at)
           VALUES (%s, %s, %s, %s)""",
        (token, user_id, email, expires_at),
    )
    conn.commit()


def consume_magic_link(conn, token: str) -> dict | None:
    """Atomically claim a valid (unused, unexpired) magic link. Returns row or None.
    Caller inspects `row["user_id"]` to discriminate sign-in vs register-intent."""
    cur = conn.cursor()
    cur.execute(
        """UPDATE magic_links
           SET used_at = NOW()
           WHERE token = %s
             AND used_at IS NULL
             AND expires_at > NOW()
           RETURNING *""",
        (token,),
    )
    row = cur.fetchone()
    conn.commit()
    return row


def get_user_by_email(conn, email: str) -> dict | None:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s OR username = %s", (email, email))
    return cur.fetchone()


def create_user_passwordless(conn, username: str, email: str) -> dict:
    """Create a user with no password — they sign in via magic link only.
    Used by the email-only signup flow."""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO users (username, password_hash, email)
           VALUES (%s, NULL, %s)
           RETURNING id, username, created_at""",
        (username, email),
    )
    row = cur.fetchone()
    conn.commit()
    return row


# --- Posts ---

import re as _re
import unicodedata as _ud

_POSTS_COLS = "id, user_id, title, slug, body, published, published_at, created_at, updated_at"


def _slugify(text: str) -> str:
    """Lowercase, ASCII-fold, collapse non-alphanumerics to '-', trim, cap at 80 chars."""
    if not text:
        return ""
    norm = _ud.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    norm = _re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm[:80].rstrip("-")


def _unique_slug(conn, user_id: int, base: str, exclude_post_id: int | None = None) -> str:
    """Ensure the slug is unique for this user; append -2, -3, ... if needed."""
    if not base:
        return ""
    cur = conn.cursor()
    candidate = base
    n = 2
    while True:
        if exclude_post_id is None:
            cur.execute(
                "SELECT 1 FROM posts WHERE user_id = %s AND slug = %s LIMIT 1",
                (user_id, candidate),
            )
        else:
            cur.execute(
                "SELECT 1 FROM posts WHERE user_id = %s AND slug = %s AND id <> %s LIMIT 1",
                (user_id, candidate, exclude_post_id),
            )
        if cur.fetchone() is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def create_post(
    conn, user_id: int, body: str, title: str = "", published: bool = False
) -> int:
    cur = conn.cursor()
    slug = _unique_slug(conn, user_id, _slugify(title))
    if published:
        cur.execute(
            """INSERT INTO posts (user_id, title, slug, body, published, published_at, updated_at)
               VALUES (%s, %s, %s, %s, TRUE, NOW(), NOW())
               RETURNING id""",
            (user_id, title, slug, body),
        )
    else:
        cur.execute(
            "INSERT INTO posts (user_id, title, slug, body) VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, title, slug, body),
        )
    post_id = cur.fetchone()["id"]
    conn.commit()
    return post_id


def get_post(conn, post_id: int, user_id: int) -> dict | None:
    cur = conn.cursor()
    cur.execute(
        f"SELECT {_POSTS_COLS} FROM posts WHERE id = %s AND user_id = %s",
        (post_id, user_id),
    )
    return cur.fetchone()


def list_all_public_posts(conn) -> list[dict]:
    """Every published post across all users — drives the global posts tab."""
    cur = conn.cursor()
    cur.execute(
        """SELECT p.id, p.title, p.slug, p.published_at, p.created_at, u.username
             FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.published = TRUE
            ORDER BY p.published_at DESC NULLS LAST, p.id DESC
            LIMIT 200""",
    )
    return cur.fetchall()


def list_public_posts_by_username(conn, username: str) -> list[dict]:
    """All published posts for a username, newest first. Public endpoint — no auth."""
    cur = conn.cursor()
    cur.execute(
        """SELECT p.id, p.title, p.slug, p.published_at, p.created_at, u.username
             FROM posts p JOIN users u ON u.id = p.user_id
            WHERE u.username = %s AND p.published = TRUE
            ORDER BY p.published_at DESC NULLS LAST, p.id DESC""",
        (username,),
    )
    return cur.fetchall()


def get_public_post_by_slug(conn, username: str, slug: str) -> dict | None:
    """Fetch a published post by (username, slug). Returns None unless published=TRUE."""
    cur = conn.cursor()
    cur.execute(
        f"""SELECT p.id, p.title, p.slug, p.body, p.published_at, p.created_at, p.updated_at,
                   u.username
              FROM posts p JOIN users u ON u.id = p.user_id
             WHERE u.username = %s AND p.slug = %s AND p.published = TRUE""",
        (username, slug),
    )
    return cur.fetchone()


def list_posts(conn, user_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        f"""SELECT {_POSTS_COLS}, (SELECT username FROM users WHERE id = %s) AS username
              FROM posts WHERE user_id = %s
             ORDER BY COALESCE(updated_at, created_at) DESC""",
        (user_id, user_id),
    )
    return cur.fetchall()


def update_post(
    conn,
    post_id: int,
    user_id: int,
    body: str | None = None,
    title: str | None = None,
    published: bool | None = None,
) -> bool:
    """Patch a post's body/title/published flag. Stamps updated_at; sets published_at on first publish.
    If title changes, regenerates slug (kept unique per user)."""
    sets = ["updated_at = NOW()"]
    params: list = []
    if body is not None:
        sets.append("body = %s")
        params.append(body)
    if title is not None:
        new_slug = _unique_slug(conn, user_id, _slugify(title), exclude_post_id=post_id)
        sets.append("title = %s")
        params.append(title)
        sets.append("slug = %s")
        params.append(new_slug)
    if published is True:
        sets.append("published = TRUE")
        sets.append("published_at = COALESCE(published_at, NOW())")
    elif published is False:
        sets.append("published = FALSE")
    params.extend([post_id, user_id])
    cur = conn.cursor()
    cur.execute(
        f"UPDATE posts SET {', '.join(sets)} WHERE id = %s AND user_id = %s",
        params,
    )
    conn.commit()
    return cur.rowcount > 0


def delete_post(conn, post_id: int, user_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("DELETE FROM posts WHERE id = %s AND user_id = %s", (post_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def sync_post_snippets(conn, post_id: int, snippet_ids: list[int], user_id: int) -> list[int]:
    """Replace post_snippets rows for a post with the given snippet_ids, filtered to snippets the user owns.
    Returns the actual stored snippet_ids."""
    cur = conn.cursor()
    cur.execute("DELETE FROM post_snippets WHERE post_id = %s", (post_id,))
    if snippet_ids:
        # Keep only snippets that exist and belong to the user.
        placeholders = ",".join("%s" for _ in snippet_ids)
        cur.execute(
            f"SELECT id FROM snippets WHERE id IN ({placeholders}) AND user_id = %s",
            snippet_ids + [user_id],
        )
        valid_ids = [r["id"] for r in cur.fetchall()]
        for nid in valid_ids:
            cur.execute(
                "INSERT INTO post_snippets (post_id, snippet_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (post_id, nid),
            )
    else:
        valid_ids = []
    conn.commit()
    return valid_ids


def get_post_snippet_ids(conn, post_id: int) -> list[int]:
    cur = conn.cursor()
    cur.execute(
        "SELECT snippet_id FROM post_snippets WHERE post_id = %s ORDER BY snippet_id",
        (post_id,),
    )
    return [r["snippet_id"] for r in cur.fetchall()]

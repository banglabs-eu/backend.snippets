"""Posts: long-form text composed from multiple snippets, optionally published."""

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db
from deps import get_conn, get_user_id, get_username, to_dict, to_list
from revalidate import (
    TAG_DASHBOARD_GLOBAL,
    TAG_POSTS_ALL,
    revalidate,
    tag_dashboard_user,
    tag_post,
    tag_posts_user,
    tag_snippet,
)


router = APIRouter()

# Reference syntax: [snippet:NNN] anywhere in the body. Used to derive
# post_snippets rows from the markdown so the join table stays in sync.
_REF_RE = re.compile(r"\[snippet:(\d+)\]")


def _extract_snippet_ids(body: str) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for m in _REF_RE.finditer(body or ""):
        nid = int(m.group(1))
        if nid not in seen:
            seen.add(nid)
            ordered.append(nid)
    return ordered


# --- Models ---

class CreatePostBody(BaseModel):
    body: str = ""
    title: str = ""
    published: bool = False
    public_tag_ids: list[int] = []


class UpdatePostBody(BaseModel):
    body: str | None = None
    title: str | None = None
    published: bool | None = None
    public_tag_ids: list[int] = []


def _require_title_to_publish(title: str | None, current_title: str = "") -> None:
    """A post must have a non-empty title before it can be published — the public URL needs a slug."""
    effective = title if title is not None else current_title
    if not (effective or "").strip():
        raise HTTPException(status_code=400, detail="A post needs a title before it can be published.")


# --- Authed endpoints ---

def _propagate_publish_to_snippets(conn, body_text: str, user_id: int, username: str, public_tag_ids: list[int]) -> None:
    """When a post is (re)published, mark each referenced snippet public and
    surface the user-selected tags. publish_snippet silently no-ops on snippets
    the user doesn't own."""
    for nid in _extract_snippet_ids(body_text or ""):
        db.publish_snippet(conn, nid, user_id, public_tag_ids)
        revalidate(tag_snippet(username, nid), tag_dashboard_user(username), TAG_DASHBOARD_GLOBAL)


@router.post("/posts")
def create_post(body: CreatePostBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if body.published:
        _require_title_to_publish(body.title)
    post_id = db.create_post(conn, uid, body.body, title=body.title, published=body.published)
    db.sync_post_snippets(conn, post_id, _extract_snippet_ids(body.body), uid)
    if body.published:
        username = get_username(request)
        _propagate_publish_to_snippets(conn, body.body, uid, username, body.public_tag_ids)
        post = db.get_post(conn, post_id, uid)
        revalidate(TAG_POSTS_ALL, tag_posts_user(username), tag_post(username, post["slug"]))
    return {"id": post_id}


@router.get("/posts")
def list_posts(request: Request):
    return to_list(db.list_posts(get_conn(request), get_user_id(request)))


@router.get("/posts/{post_id}")
def get_post(post_id: int, request: Request):
    conn = get_conn(request)
    row = db.get_post(conn, post_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Post not found")
    out = to_dict(row)
    out["snippet_ids"] = db.get_post_snippet_ids(conn, post_id)
    return out


@router.patch("/posts/{post_id}")
def update_post(post_id: int, body: UpdatePostBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    current = db.get_post(conn, post_id, uid)
    if current is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if body.published is True:
        _require_title_to_publish(body.title, current.get("title", ""))
    db.update_post(conn, post_id, uid, body=body.body, title=body.title, published=body.published)
    if body.body is not None:
        db.sync_post_snippets(conn, post_id, _extract_snippet_ids(body.body), uid)

    was_published = bool(current.get("published"))
    if was_published or body.published is True:
        username = get_username(request)
        if body.published is True:
            # Propagate publish to referenced snippets + selected tags. Use the new body if
            # provided, otherwise fall back to the existing one.
            body_text = body.body if body.body is not None else current.get("body", "")
            _propagate_publish_to_snippets(conn, body_text, uid, username, body.public_tag_ids)
        # Re-fetch rather than trust `body` alone: a title-only rename regenerates the slug
        # even when `published` wasn't part of this patch, and both the old and new slug's
        # cached pages need to drop.
        new_row = db.get_post(conn, post_id, uid)
        tags_to_bust = {TAG_POSTS_ALL, tag_posts_user(username)}
        if was_published:
            tags_to_bust.add(tag_post(username, current["slug"]))
        if new_row["published"]:
            tags_to_bust.add(tag_post(username, new_row["slug"]))
        revalidate(*tags_to_bust)
    return {"ok": True}


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    current = db.get_post(conn, post_id, uid)
    if current is None:
        raise HTTPException(status_code=404, detail="Post not found")
    db.delete_post(conn, post_id, uid)
    if current.get("published"):
        username = get_username(request)
        revalidate(TAG_POSTS_ALL, tag_posts_user(username), tag_post(username, current["slug"]))
    return {"ok": True}


# --- Public endpoints (no auth, only published posts) ---

@router.get("/public/users/{username}/posts")
def list_public_user_posts(username: str, request: Request):
    return to_list(db.list_public_posts_by_username(get_conn(request), username))


@router.get("/public/users/{username}/dashboard")
def get_public_user_dashboard(username: str, request: Request):
    """Aggregated public dataset shaped for the Dashboard UI."""
    data = db.get_public_dashboard(get_conn(request), username)
    return {
        "snippets": to_list(data["snippets"]),
        "tags": to_list(data["tags"]),
        "sources": to_list(data["sources"]),
        "authors": to_list(data["authors"]),
        "publishers": to_list(data["publishers"]),
        "source_types": to_list(data["source_types"]),
        "snippet_tags": {str(k): to_list(v) for k, v in data["snippet_tags"].items()},
    }


@router.get("/public/users/{username}/snippets")
def list_public_user_snippets(username: str, request: Request):
    conn = get_conn(request)
    rows = to_list(db.list_public_snippets_by_username(conn, username))
    snippet_ids = [r["id"] for r in rows]
    tag_map = db.get_public_tags_for_snippets(conn, snippet_ids)
    for r in rows:
        r["tags"] = tag_map.get(r["id"], [])
    return rows


@router.get("/public/users/{username}/snippets/{snippet_id}")
def get_public_user_snippet(username: str, snippet_id: int, request: Request):
    conn = get_conn(request)
    row = db.get_public_snippet(conn, username, snippet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    out = to_dict(row)
    tag_map = db.get_public_tags_for_snippets(conn, [snippet_id])
    out["tags"] = tag_map.get(snippet_id, [])
    return out


@router.get("/public/feed/dashboard")
def public_feed_dashboard(request: Request):
    """Cross-user version of the Dashboard payload — drives the global /public view."""
    data = db.get_global_public_dashboard(get_conn(request))
    return {
        "snippets": to_list(data["snippets"]),
        "tags": to_list(data["tags"]),
        "sources": to_list(data["sources"]),
        "authors": to_list(data["authors"]),
        "publishers": to_list(data["publishers"]),
        "source_types": to_list(data["source_types"]),
        "snippet_tags": {str(k): to_list(v) for k, v in data["snippet_tags"].items()},
    }


@router.get("/public/posts")
def list_all_public_posts(request: Request):
    """Every published post across all users — drives the global posts tab."""
    return to_list(db.list_all_public_posts(get_conn(request)))


@router.get("/public/posts/{username}/{slug}")
def get_public_post_by_slug(username: str, slug: str, request: Request):
    row = db.get_public_post_by_slug(get_conn(request), username, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return to_dict(row)

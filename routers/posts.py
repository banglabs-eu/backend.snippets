"""Posts: long-form text composed from multiple notes, optionally published."""

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db
from deps import get_conn, get_user_id, to_dict, to_list


router = APIRouter()

# Reference syntax: [snippet:NNN] anywhere in the body. Used to derive post_notes
# rows from the markdown so the join table stays in sync with the visible refs.
_REF_RE = re.compile(r"\[snippet:(\d+)\]")


def _extract_note_ids(body: str) -> list[int]:
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
    published: bool = False


class UpdatePostBody(BaseModel):
    body: str | None = None
    published: bool | None = None


# --- Authed endpoints ---

@router.post("/posts")
def create_post(body: CreatePostBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    post_id = db.create_post(conn, uid, body.body, published=body.published)
    db.sync_post_notes(conn, post_id, _extract_note_ids(body.body), uid)
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
    out["note_ids"] = db.get_post_note_ids(conn, post_id)
    return out


@router.patch("/posts/{post_id}")
def update_post(post_id: int, body: UpdatePostBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_post(conn, post_id, uid) is None:
        raise HTTPException(status_code=404, detail="Post not found")
    db.update_post(conn, post_id, uid, body=body.body, published=body.published)
    if body.body is not None:
        db.sync_post_notes(conn, post_id, _extract_note_ids(body.body), uid)
    return {"ok": True}


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if not db.delete_post(conn, post_id, uid):
        raise HTTPException(status_code=404, detail="Post not found")
    return {"ok": True}


# --- Public endpoint (no auth, only published posts) ---

@router.get("/public/posts/{post_id}")
def get_public_post(post_id: int, request: Request):
    row = db.get_public_post(get_conn(request), post_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return to_dict(row)

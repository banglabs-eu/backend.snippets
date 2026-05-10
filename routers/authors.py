"""Author endpoints."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

import db
from deps import get_conn, get_user_id, to_dict, to_list


router = APIRouter(prefix="/authors")


class UpdateAuthorBody(BaseModel):
    first_name: str | None = None
    last_name: str | None = None


@router.get("")
def get_all_authors(request: Request):
    return to_list(db.get_all_authors(get_conn(request), get_user_id(request)))


@router.get("/recent")
def get_recent_authors(request: Request):
    return to_list(db.get_recent_authors(get_conn(request), get_user_id(request)))


@router.get("/search")
def search_authors(request: Request, q: str = Query(default="")):
    return to_list(db.search_authors(get_conn(request), q, get_user_id(request)))


@router.get("/last-names")
def search_author_last_names(request: Request, q: str = Query(default="")):
    return db.search_author_last_names(get_conn(request), q, get_user_id(request))


@router.get("/first-names")
def search_author_first_names(request: Request, q: str = Query(default="")):
    return db.search_author_first_names(get_conn(request), q, get_user_id(request))


@router.patch("/{author_id}")
def update_author(author_id: int, body: UpdateAuthorBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_author(conn, author_id, uid) is None:
        raise HTTPException(status_code=404, detail="Author not found")
    row = db.update_author(conn, author_id, uid, body.first_name, body.last_name)
    return to_dict(row)


@router.delete("/{author_id}")
def delete_author(author_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_author(conn, author_id, uid) is None:
        raise HTTPException(status_code=404, detail="Author not found")
    db.delete_author(conn, author_id, uid)
    return {"ok": True}

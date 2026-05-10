"""Tag endpoints."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

import db
from deps import get_conn, get_user_id, to_dict, to_list


router = APIRouter(prefix="/tags")


class GetOrCreateTagBody(BaseModel):
    name: str


@router.get("/recent")
def get_recent_tags(request: Request):
    return to_list(db.get_recent_tags(get_conn(request), get_user_id(request)))


@router.get("/search")
def search_tags(request: Request, q: str = Query(default="")):
    return to_list(db.search_tags(get_conn(request), q, get_user_id(request)))


@router.get("/by-name")
def get_tag_by_name(request: Request, name: str = Query()):
    row = db.get_tag_by_name(get_conn(request), name, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return to_dict(row)


@router.post("/get-or-create")
def get_or_create_tag(body: GetOrCreateTagBody, request: Request):
    tag_id = db.get_or_create_tag(get_conn(request), body.name, get_user_id(request))
    return {"id": tag_id}


@router.get("")
def get_all_tags(request: Request):
    return to_list(db.get_all_tags(get_conn(request), get_user_id(request)))


@router.get("/{tag_id}")
def get_tag(tag_id: int, request: Request):
    row = db.get_tag(get_conn(request), tag_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return to_dict(row)


@router.delete("/{tag_id}")
def delete_tag(tag_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_tag(conn, tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.delete_tag(conn, tag_id, uid)
    return {"ok": True}

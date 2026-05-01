"""Source endpoints."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

import db
from deps import get_conn, get_user_id, to_dict, to_list


router = APIRouter(prefix="/sources")


class CreateSourceBody(BaseModel):
    name: str
    source_type_id: int | None = None
    source_type: str | None = None  # convenience: resolve/create by name
    year: str | None = None
    url: str | None = None
    accessed_date: str | None = None
    edition: str | None = None
    pages: str | None = None
    extra_notes: str | None = None
    publisher_id: int | None = None
    location: str | None = None
    date: str | None = None


class UpdateSourceBody(BaseModel):
    name: str | None = None
    source_type_id: int | None = None
    source_type: str | None = None
    year: str | None = None
    url: str | None = None
    accessed_date: str | None = None
    edition: str | None = None
    pages: str | None = None
    extra_notes: str | None = None
    publisher_id: int | None = None
    location: str | None = None
    date: str | None = None


class AddAuthorBody(BaseModel):
    first_name: str
    last_name: str
    order: int


def _resolve_source_type_id(conn, body) -> int | None:
    """Prefer explicit source_type_id; otherwise resolve/create by name."""
    if body.source_type_id is not None:
        return body.source_type_id
    if body.source_type and body.source_type.strip():
        return db.get_or_create_source_type_by_name(conn, body.source_type)
    return None


@router.post("")
def create_source(body: CreateSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if body.publisher_id is not None and db.get_publisher(conn, body.publisher_id, uid) is None:
        raise HTTPException(status_code=404, detail="Publisher not found")
    source_type_id = _resolve_source_type_id(conn, body)
    source_id = db.create_source(
        conn, body.name, uid,
        source_type_id=source_type_id,
        year=body.year,
        url=body.url,
        accessed_date=body.accessed_date,
        edition=body.edition,
        pages=body.pages,
        extra_notes=body.extra_notes,
        publisher_id=body.publisher_id,
        location=body.location,
        date=body.date,
    )
    return {"id": source_id}


@router.get("/recent")
def get_recent_sources(request: Request):
    return to_list(db.get_recent_sources(get_conn(request), get_user_id(request)))


@router.get("/search")
def search_sources(request: Request, q: str = Query(default="")):
    return to_list(db.search_sources(get_conn(request), q, get_user_id(request)))


@router.get("")
def get_sources(
    request: Request,
    author_last: str | None = Query(default=None),
    author_first: str | None = Query(default=None),
):
    conn = get_conn(request)
    uid = get_user_id(request)
    if author_last is not None and author_first is not None:
        return to_list(db.get_sources_by_author(conn, author_last, author_first, uid))
    return to_list(db.get_all_sources(conn, uid))


@router.get("/{source_id}/citation")
def get_citation(source_id: int, request: Request):
    citation = db.build_citation(get_conn(request), source_id, get_user_id(request))
    return {"citation": citation}


@router.get("/{source_id}/authors")
def get_authors_for_source(source_id: int, request: Request):
    conn = get_conn(request)
    if db.get_source(conn, source_id, get_user_id(request)) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return to_list(db.get_authors_for_source(conn, source_id))


@router.post("/{source_id}/authors")
def add_author(source_id: int, body: AddAuthorBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_source(conn, source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    author_id = db.add_author(conn, source_id, body.first_name, body.last_name, body.order)
    return {"id": author_id}


@router.get("/{source_id}")
def get_source(source_id: int, request: Request):
    row = db.get_source(get_conn(request), source_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return to_dict(row)


@router.patch("/{source_id}")
def update_source(source_id: int, body: UpdateSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_source(conn, source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if body.publisher_id is not None and db.get_publisher(conn, body.publisher_id, uid) is None:
        raise HTTPException(status_code=404, detail="Publisher not found")

    fields = body.model_dump(exclude_unset=True, exclude={"source_type"})
    if body.source_type is not None and body.source_type_id is None:
        fields["source_type_id"] = (
            db.get_or_create_source_type_by_name(conn, body.source_type)
            if body.source_type.strip()
            else None
        )

    row = db.update_source(conn, source_id, uid, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return to_dict(row)

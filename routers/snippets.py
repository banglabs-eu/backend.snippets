"""Snippet endpoints, including Anki export."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

import anki_export
import db
from deps import get_conn, get_user_id, to_dict, to_list


router = APIRouter(prefix="/snippets")


# --- Models ---

class CreateSnippetBody(BaseModel):
    body: str
    source_id: int | None = None
    locator_type: str | None = None
    locator_value: str | None = None


class UpdateSnippetSourceBody(BaseModel):
    source_id: int


class UpdateSnippetBodyRequest(BaseModel):
    body: str


class SnippetIdsBody(BaseModel):
    snippet_ids: list[int]


class BulkSourceBody(BaseModel):
    snippet_ids: list[int]
    source_id: int


class PublishSnippetBody(BaseModel):
    public_tag_ids: list[int] = []


class AddTagToSnippetBody(BaseModel):
    tag_id: int


# --- Endpoints ---

@router.post("")
def create_snippet(body: CreateSnippetBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if body.source_id is not None and db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    snippet_id = db.create_snippet(
        conn, body.body, uid,
        source_id=body.source_id,
        locator_type=body.locator_type,
        locator_value=body.locator_value,
    )
    return {"id": snippet_id}


@router.post("/sourceless-check")
def get_sourceless_snippets(body: SnippetIdsBody, request: Request):
    return db.get_sourceless_snippets(get_conn(request), body.snippet_ids, get_user_id(request))


@router.post("/bulk-source")
def bulk_update_snippet_source(body: BulkSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.bulk_update_snippet_source(conn, body.snippet_ids, body.source_id, uid)
    return {"ok": True}


@router.post("/tags/batch")
def get_tags_for_snippets(body: SnippetIdsBody, request: Request):
    result = db.get_tags_for_snippets(get_conn(request), body.snippet_ids, get_user_id(request))
    return {str(k): to_list(v) for k, v in result.items()}


@router.get("/search")
def search_snippets(request: Request, q: str = Query(default="")):
    if not q.strip():
        return []
    return to_list(db.search_snippets(get_conn(request), q.strip(), get_user_id(request)))


@router.post("/export/anki")
def export_anki(body: SnippetIdsBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if not body.snippet_ids:
        raise HTTPException(status_code=400, detail="No snippets selected")

    # Fetch snippets the user actually owns.
    snippets_by_id: dict[int, dict] = {}
    for nid in body.snippet_ids:
        row = db.get_snippet(conn, nid, uid)
        if row is not None:
            snippets_by_id[nid] = dict(row)
    if not snippets_by_id:
        raise HTTPException(status_code=404, detail="No matching snippets found")

    # Preserve the requested order.
    ordered_snippets = [snippets_by_id[nid] for nid in body.snippet_ids if nid in snippets_by_id]

    # Sources for citation rendering.
    source_ids = {n["source_id"] for n in ordered_snippets if n.get("source_id")}
    sources_by_id: dict[int, dict] = {}
    for sid in source_ids:
        src = db.get_source(conn, sid, uid)
        if src:
            sources_by_id[sid] = dict(src)

    # Tags per snippet.
    tags_raw = db.get_tags_for_snippets(conn, list(snippets_by_id.keys()), uid)
    tags_by_snippet = {nid: [t["name"] for t in tags] for nid, tags in tags_raw.items()}

    apkg_bytes = anki_export.build_apkg(ordered_snippets, sources_by_id, tags_by_snippet)
    return Response(
        content=apkg_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="snippets.apkg"'},
    )


@router.get("")
def get_snippets(
    request: Request,
    source_id: int | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    author_id: int | None = Query(default=None),
):
    conn = get_conn(request)
    uid = get_user_id(request)
    if source_id is not None:
        return to_list(db.get_snippets_by_source(conn, source_id, uid))
    if tag_id is not None:
        return to_list(db.get_snippets_by_tag(conn, tag_id, uid))
    if author_id is not None:
        return to_list(db.get_snippets_by_author(conn, author_id, uid))
    return to_list(db.get_all_snippets(conn, uid))


@router.get("/{snippet_id}")
def get_snippet(snippet_id: int, request: Request):
    row = db.get_snippet(get_conn(request), snippet_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    return to_dict(row)


@router.patch("/{snippet_id}/source")
def update_snippet_source(snippet_id: int, body: UpdateSnippetSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_snippet(conn, snippet_id, uid) is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.update_snippet_source(conn, snippet_id, body.source_id, uid)
    return {"ok": True}


@router.patch("/{snippet_id}/body")
def update_snippet_body(snippet_id: int, body: UpdateSnippetBodyRequest, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_snippet(conn, snippet_id, uid) is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    db.update_snippet_body(conn, snippet_id, body.body, uid)
    return {"ok": True}


@router.get("/{snippet_id}/tags")
def get_tags_for_snippet(snippet_id: int, request: Request):
    conn = get_conn(request)
    if db.get_snippet(conn, snippet_id, get_user_id(request)) is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    return to_list(db.get_tags_for_snippet(conn, snippet_id))


@router.post("/{snippet_id}/tags")
def add_tag_to_snippet(snippet_id: int, body: AddTagToSnippetBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_snippet(conn, snippet_id, uid) is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    if db.get_tag(conn, body.tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.add_tag_to_snippet(conn, snippet_id, body.tag_id)
    return {"ok": True}


@router.post("/{snippet_id}/publish")
def publish_snippet(snippet_id: int, body: PublishSnippetBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if not db.publish_snippet(conn, snippet_id, uid, body.public_tag_ids):
        raise HTTPException(status_code=404, detail="Snippet not found")
    return {"ok": True}


@router.post("/{snippet_id}/unpublish")
def unpublish_snippet(snippet_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if not db.unpublish_snippet(conn, snippet_id, uid):
        raise HTTPException(status_code=404, detail="Snippet not found")
    return {"ok": True}


@router.delete("/{snippet_id}")
def delete_snippet(snippet_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_snippet(conn, snippet_id, uid) is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    db.delete_snippet(conn, snippet_id, uid)
    return {"ok": True}


@router.delete("/{snippet_id}/tags/{tag_id}")
def remove_tag_from_snippet(snippet_id: int, tag_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_snippet(conn, snippet_id, uid) is None:
        raise HTTPException(status_code=404, detail="Snippet not found")
    if db.get_tag(conn, tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.remove_tag_from_snippet(conn, snippet_id, tag_id)
    return {"ok": True}

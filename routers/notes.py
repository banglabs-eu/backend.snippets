"""Note endpoints, including Anki export."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

import anki_export
import db
from deps import get_conn, get_user_id, to_dict, to_list


router = APIRouter(prefix="/notes")


# --- Models ---

class CreateNoteBody(BaseModel):
    body: str
    source_id: int | None = None
    locator_type: str | None = None
    locator_value: str | None = None


class UpdateNoteSourceBody(BaseModel):
    source_id: int


class UpdateNoteBodyRequest(BaseModel):
    body: str


class NoteIdsBody(BaseModel):
    note_ids: list[int]


class BulkSourceBody(BaseModel):
    note_ids: list[int]
    source_id: int


class AddTagToNoteBody(BaseModel):
    tag_id: int


# --- Endpoints ---

@router.post("")
def create_note(body: CreateNoteBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if body.source_id is not None and db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    note_id = db.create_note(
        conn, body.body, uid,
        source_id=body.source_id,
        locator_type=body.locator_type,
        locator_value=body.locator_value,
    )
    return {"id": note_id}


@router.post("/sourceless-check")
def get_sourceless_notes(body: NoteIdsBody, request: Request):
    return db.get_sourceless_notes(get_conn(request), body.note_ids, get_user_id(request))


@router.post("/bulk-source")
def bulk_update_note_source(body: BulkSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.bulk_update_note_source(conn, body.note_ids, body.source_id, uid)
    return {"ok": True}


@router.post("/tags/batch")
def get_tags_for_notes(body: NoteIdsBody, request: Request):
    result = db.get_tags_for_notes(get_conn(request), body.note_ids, get_user_id(request))
    return {str(k): to_list(v) for k, v in result.items()}


@router.get("/search")
def search_notes(request: Request, q: str = Query(default="")):
    if not q.strip():
        return []
    return to_list(db.search_notes(get_conn(request), q.strip(), get_user_id(request)))


@router.post("/export/anki")
def export_anki(body: NoteIdsBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if not body.note_ids:
        raise HTTPException(status_code=400, detail="No notes selected")

    # Fetch notes the user actually owns.
    notes_by_id: dict[int, dict] = {}
    for nid in body.note_ids:
        row = db.get_note(conn, nid, uid)
        if row is not None:
            notes_by_id[nid] = dict(row)
    if not notes_by_id:
        raise HTTPException(status_code=404, detail="No matching notes found")

    # Preserve the requested order.
    ordered_notes = [notes_by_id[nid] for nid in body.note_ids if nid in notes_by_id]

    # Sources for citation rendering.
    source_ids = {n["source_id"] for n in ordered_notes if n.get("source_id")}
    sources_by_id: dict[int, dict] = {}
    for sid in source_ids:
        src = db.get_source(conn, sid, uid)
        if src:
            sources_by_id[sid] = dict(src)

    # Tags per note.
    tags_raw = db.get_tags_for_notes(conn, list(notes_by_id.keys()), uid)
    tags_by_note = {nid: [t["name"] for t in tags] for nid, tags in tags_raw.items()}

    apkg_bytes = anki_export.build_apkg(ordered_notes, sources_by_id, tags_by_note)
    return Response(
        content=apkg_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="snippets.apkg"'},
    )


@router.get("")
def get_notes(
    request: Request,
    source_id: int | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    author_id: int | None = Query(default=None),
):
    conn = get_conn(request)
    uid = get_user_id(request)
    if source_id is not None:
        return to_list(db.get_notes_by_source(conn, source_id, uid))
    if tag_id is not None:
        return to_list(db.get_notes_by_tag(conn, tag_id, uid))
    if author_id is not None:
        return to_list(db.get_notes_by_author(conn, author_id, uid))
    return to_list(db.get_all_notes(conn, uid))


@router.get("/{note_id}")
def get_note(note_id: int, request: Request):
    row = db.get_note(get_conn(request), note_id, get_user_id(request))
    if row is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return to_dict(row)


@router.patch("/{note_id}/source")
def update_note_source(note_id: int, body: UpdateNoteSourceBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_source(conn, body.source_id, uid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    db.update_note_source(conn, note_id, body.source_id, uid)
    return {"ok": True}


@router.patch("/{note_id}/body")
def update_note_body(note_id: int, body: UpdateNoteBodyRequest, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    db.update_note_body(conn, note_id, body.body, uid)
    return {"ok": True}


@router.get("/{note_id}/tags")
def get_tags_for_note(note_id: int, request: Request):
    conn = get_conn(request)
    if db.get_note(conn, note_id, get_user_id(request)) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return to_list(db.get_tags_for_note(conn, note_id))


@router.post("/{note_id}/tags")
def add_tag_to_note(note_id: int, body: AddTagToNoteBody, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_tag(conn, body.tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.add_tag_to_note(conn, note_id, body.tag_id)
    return {"ok": True}


@router.delete("/{note_id}")
def delete_note(note_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete_note(conn, note_id, uid)
    return {"ok": True}


@router.delete("/{note_id}/tags/{tag_id}")
def remove_tag_from_note(note_id: int, tag_id: int, request: Request):
    conn = get_conn(request)
    uid = get_user_id(request)
    if db.get_note(conn, note_id, uid) is None:
        raise HTTPException(status_code=404, detail="Note not found")
    if db.get_tag(conn, tag_id, uid) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    db.remove_tag_from_note(conn, note_id, tag_id)
    return {"ok": True}

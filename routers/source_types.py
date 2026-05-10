"""Source type endpoints (shared across users)."""

import psycopg2.errors
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db
from deps import get_conn, to_dict, to_list


router = APIRouter(prefix="/source-types")


class CreateSourceTypeBody(BaseModel):
    name: str


@router.get("")
def get_source_types(request: Request):
    return to_list(db.get_source_types(get_conn(request)))


@router.post("")
def create_source_type(body: CreateSourceTypeBody, request: Request):
    conn = get_conn(request)
    try:
        type_id = db.create_source_type(conn, body.name)
        return {"id": type_id}
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=409, detail=f"Source type '{body.name}' already exists")


@router.get("/{type_id}")
def get_source_type(type_id: int, request: Request):
    row = db.get_source_type(get_conn(request), type_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Source type not found")
    return to_dict(row)

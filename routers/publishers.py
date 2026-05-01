"""Publisher endpoints."""

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

import db
from deps import get_conn, get_user_id, to_list


router = APIRouter(prefix="/publishers")


class GetOrCreatePublisherBody(BaseModel):
    name: str
    city: str | None = None


@router.get("/search")
def search_publishers(request: Request, q: str = Query(default="")):
    return to_list(db.search_publishers(get_conn(request), q, get_user_id(request)))


@router.get("/cities")
def search_publisher_cities(request: Request, q: str = Query(default="")):
    return db.search_publisher_cities(get_conn(request), q, get_user_id(request))


@router.post("/get-or-create")
def get_or_create_publisher(body: GetOrCreatePublisherBody, request: Request):
    pub_id = db.get_or_create_publisher(get_conn(request), body.name, get_user_id(request), body.city)
    return {"id": pub_id}

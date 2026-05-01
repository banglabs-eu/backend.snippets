"""Admin-only endpoints for managing invite codes."""

import os

from fastapi import APIRouter, HTTPException, Request

import db
from deps import get_conn, get_user_id, get_username, to_list


router = APIRouter(prefix="/invite-codes")

_INVITE_ADMIN = os.environ.get("INVITE_ADMIN", "adam")


def _require_admin(request: Request):
    if get_username(request) != _INVITE_ADMIN:
        raise HTTPException(status_code=403, detail="Only the invite admin can manage invite codes")


@router.post("")
def create_invite_code(request: Request):
    _require_admin(request)
    code = db.create_invite_code(get_conn(request), created_by=get_user_id(request))
    return {"code": code}


@router.get("")
def list_invite_codes(request: Request):
    _require_admin(request)
    return to_list(db.get_invite_codes(get_conn(request), get_user_id(request)))

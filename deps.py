"""Shared dependencies and helpers used across route modules."""

from datetime import datetime
from fastapi import Request


def to_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def to_list(rows) -> list[dict]:
    return [to_dict(r) for r in rows]


def get_conn(request: Request):
    return request.state.conn


def get_user_id(request: Request) -> int:
    return request.state.user_id


def get_username(request: Request) -> str:
    return request.state.username

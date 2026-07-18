"""Best-effort webhook to public.snippets so its Next.js ISR cache drops
stale pages immediately after a publish-affecting write, instead of waiting
out the per-route revalidate window (1h for list/dashboard pages, 24h for
individual post/snippet pages — see public.snippets/src/lib/api.ts). The tag
builders below must mirror that file's `tags` export exactly, or invalidation
silently becomes a no-op mismatch rather than an error.

PUBLIC_SITE_REVALIDATE_SECRET must equal public.snippets's REVALIDATE_SECRET.
Leaving it unset (e.g. in tests, or before the public site is deployed)
disables this module entirely rather than failing.
"""

import logging
import os

import requests

logger = logging.getLogger("revalidate")

_URL = os.environ.get("PUBLIC_SITE_URL", "http://127.0.0.1:3000")
_SECRET = os.environ.get("PUBLIC_SITE_REVALIDATE_SECRET")


def revalidate(*tags: str) -> None:
    """Fire-and-forget: never raises. A dead/misconfigured public site should
    degrade to stale pages, not break the authed write path that triggered this."""
    deduped = tuple(dict.fromkeys(t for t in tags if t))
    if not _SECRET or not deduped:
        return
    try:
        requests.post(
            f"{_URL}/api/revalidate",
            json={"tags": list(deduped)},
            headers={"x-revalidate-secret": _SECRET},
            timeout=2,
        )
    except requests.RequestException:
        logger.warning("public site revalidate failed for tags=%s", deduped, exc_info=True)


# --- Tag-name builders — must match public.snippets/src/lib/api.ts `tags` ---

def tag_post(username: str, slug: str) -> str:
    return f"post:{username}:{slug}"


def tag_posts_user(username: str) -> str:
    return f"posts:user:{username}"


TAG_POSTS_ALL = "posts:all"


def tag_dashboard_user(username: str) -> str:
    return f"dashboard:user:{username}"


TAG_DASHBOARD_GLOBAL = "dashboard:global"


def tag_snippet(username: str, snippet_id: int) -> str:
    return f"snippet:{username}:{snippet_id}"

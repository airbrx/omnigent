"""Fork-local routes serving agent avatars for the agent drawer.

airbrx-only, and deliberately its own module: a file upstream does not
have can never conflict on a sync. The only upstream-owned line this
feature adds is the ``include_router`` call in ``app.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.agent_avatar_store import AgentAvatarStore

#: Raster formats only. SVG is excluded on purpose — it is an active
#: document (script, external refs) served from our own origin, so a
#: hostile upload would be stored XSS against every drawer viewer.
ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

#: 2 MiB. An avatar renders at ~40px; anything larger is a mistake, and
#: the cap keeps a bad upload from filling the artifact store.
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def create_agent_avatars_router(
    avatar_store: AgentAvatarStore,
    *,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the router for ``/v1/agent-avatars``."""
    router = APIRouter()

    @router.get("/agent-avatars")
    async def list_agent_avatars(request: Request) -> dict[str, Any]:
        require_user(request, auth_provider)
        return {
            "data": [
                {
                    "agent_name": a.agent_name,
                    "url": f"/v1/agent-avatars/{a.agent_name}",
                    "updated_at": a.updated_at,
                }
                for a in avatar_store.list_all()
            ]
        }

    @router.get("/agent-avatars/{agent_name}")
    async def get_agent_avatar(request: Request, agent_name: str) -> Response:
        require_user(request, auth_provider)
        found = avatar_store.get(agent_name)
        if found is None:
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no avatar for that agent"}},
                status_code=404,
            )
        data, meta = found
        # Keyed on updated_at so a re-upload busts the cache immediately
        # while an unchanged avatar is never refetched.
        return Response(
            content=data,
            media_type=meta.content_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "ETag": f'"{meta.updated_at}"',
            },
        )

    @router.put("/agent-avatars/{agent_name}")
    async def put_agent_avatar(request: Request, agent_name: str, file: UploadFile) -> Response:
        require_user(request, auth_provider)
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_input",
                        "message": (
                            f"unsupported content type {file.content_type!r}; "
                            f"allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
                        ),
                    }
                },
                status_code=400,
            )
        data = await file.read()
        if len(data) > MAX_AVATAR_BYTES:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalid_input",
                        "message": f"avatar too large ({len(data)} bytes, max {MAX_AVATAR_BYTES})",
                    }
                },
                status_code=400,
            )
        meta = avatar_store.put(agent_name, data, file.content_type)
        return JSONResponse(
            {
                "agent_name": meta.agent_name,
                "url": f"/v1/agent-avatars/{meta.agent_name}",
                "updated_at": meta.updated_at,
            }
        )

    @router.delete("/agent-avatars/{agent_name}")
    async def delete_agent_avatar(request: Request, agent_name: str) -> Response:
        require_user(request, auth_provider)
        if not avatar_store.delete(agent_name):
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no avatar for that agent"}},
                status_code=404,
            )
        return Response(status_code=204)

    return router

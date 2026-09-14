"""Fork-local routes serving agent avatars for the agent drawer.

airbrx-only, and deliberately its own module: a file upstream does not
have can never conflict on a sync. The only upstream-owned line this
feature adds is the ``include_router`` call in ``app.py``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.server.routes.sessions import _read_upload_capped
from omnigent.stores.agent_avatar_store import AgentAvatarStore

#: Raster formats only. SVG is excluded on purpose — it is an active
#: document (script, external refs) served from our own origin, so a
#: hostile upload would be stored XSS against every drawer viewer.
ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

#: 2 MiB. An avatar renders at ~40px; anything larger is a mistake, and
#: the cap keeps a bad upload from filling the artifact store.
MAX_AVATAR_BYTES = 2 * 1024 * 1024

#: Mirrors the ``agent_name`` column (``String(255)``) so an over-length
#: name is rejected up front rather than truncated silently on write.
MAX_AGENT_NAME_LENGTH = 255

#: Conservative charset for a name that is used both as a URL path segment
#: and as a component of an artifact-store key. Letters, digits, spaces,
#: dots, underscores and hyphens only — no ``/`` (would smuggle an extra
#: path/key segment) and no other punctuation.
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]+$")

#: Names that are themselves dot-segments. These pass ``_AGENT_NAME_RE``
#: (``.`` and ``._-`` are allowed characters) but ``PurePosixPath``
#: normalizes them away: the artifact key ``agent-avatars/<ws>/.``
#: collapses to the workspace's own directory, so ``LocalArtifactStore``
#: writes a FILE there — where every *other* avatar in that workspace
#: needs the same path as a DIRECTORY. Every later ``put()`` in that
#: workspace then fails ``parent.mkdir()`` with ``FileExistsError`` -> 500.
_RESERVED_AGENT_NAMES = frozenset({".", ".."})

#: PNG, JPEG, GIF and WebP magic-byte signatures, used to sniff the real
#: format of an upload rather than trust the client-asserted multipart
#: ``Content-Type`` header (see Finding 2: arbitrary bytes could otherwise
#: be stored — and served from our own origin — as ``image/png``).
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_GIF_SIGNATURES = (b"GIF87a", b"GIF89a")


def _validate_agent_name(agent_name: str) -> str | None:
    """Validate an agent name used as both a path segment and a storage key.

    :param agent_name: The raw, URL-decoded path parameter.
    :returns: An error message if invalid, or ``None`` if the name is safe
        to use unmodified as an artifact-store key component.
    """
    if not agent_name or len(agent_name) > MAX_AGENT_NAME_LENGTH:
        return f"agent_name must be between 1 and {MAX_AGENT_NAME_LENGTH} characters"
    if agent_name in _RESERVED_AGENT_NAMES:
        return "agent_name may not be '.' or '..'"
    if not _AGENT_NAME_RE.match(agent_name):
        return "agent_name may contain only letters, digits, spaces, '.', '_', and '-'"
    return None


def _sniff_image_content_type(data: bytes) -> str | None:
    """Identify an image's real format from its magic bytes.

    Used to prefer the actual bytes over the client's (attacker-
    controlled) multipart ``Content-Type`` header when deciding what to
    store and later serve.

    :param data: The full upload body.
    :returns: One of :data:`ALLOWED_CONTENT_TYPES`, or ``None`` when the
        bytes match no recognized signature.
    """
    if data.startswith(_PNG_SIGNATURE):
        return "image/png"
    if data.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    if data.startswith(_GIF_SIGNATURES):
        return "image/gif"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _invalid_input(message: str) -> JSONResponse:
    """Build the standard ``400`` error body used across this router."""
    return JSONResponse(
        {"error": {"code": "invalid_input", "message": message}},
        status_code=400,
    )


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
                    "url": f"/v1/agent-avatars/{quote(a.agent_name, safe='')}",
                    "updated_at": a.updated_at,
                }
                for a in avatar_store.list_all()
            ]
        }

    @router.get(
        "/agent-avatars/{agent_name}",
        response_class=Response,
        responses={
            200: {
                "description": "The agent's avatar image",
                "content": {
                    media_type: {"schema": {"type": "string", "format": "binary"}}
                    for media_type in sorted(ALLOWED_CONTENT_TYPES)
                },
            },
            400: {"description": "agent_name failed validation"},
            404: {"description": "No avatar stored for that agent"},
        },
    )
    async def get_agent_avatar(request: Request, agent_name: str) -> Response:
        require_user(request, auth_provider)
        name_error = _validate_agent_name(agent_name)
        if name_error is not None:
            return _invalid_input(name_error)
        found = avatar_store.get(agent_name)
        if found is None:
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no avatar for that agent"}},
                status_code=404,
            )
        data, meta = found
        # Keyed on updated_at so a re-upload busts the cache immediately
        # while an unchanged avatar is never refetched. nosniff: the
        # stored content type is derived from the upload's own magic
        # bytes (see _sniff_image_content_type), but a browser must still
        # be told not to second-guess it — without this header a crafted
        # payload stored under an image type can still be MIME-sniffed
        # and executed by the browser.
        return Response(
            content=data,
            media_type=meta.content_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "ETag": f'"{meta.updated_at}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.put("/agent-avatars/{agent_name}")
    async def put_agent_avatar(request: Request, agent_name: str, file: UploadFile) -> Response:
        require_user(request, auth_provider)
        name_error = _validate_agent_name(agent_name)
        if name_error is not None:
            return _invalid_input(name_error)
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            return _invalid_input(
                f"unsupported content type {file.content_type!r}; "
                f"allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
            )
        # Chunked, capped read: aborts with 413 as soon as the upload
        # crosses MAX_AVATAR_BYTES instead of buffering the whole body
        # (Starlette spools to disk, then heap) before ever checking size.
        data = await _read_upload_capped(file, MAX_AVATAR_BYTES)
        # Prefer the sniffed type over the client's claim: the allowlist
        # above only gates the declared multipart header, which is
        # entirely client-controlled, so arbitrary bytes could otherwise
        # be stored (and later served) as any allowed image type.
        sniffed_type = _sniff_image_content_type(data)
        if sniffed_type is None:
            return _invalid_input(
                "upload does not match a supported image format (png/jpeg/webp/gif)"
            )
        meta = avatar_store.put(agent_name, data, sniffed_type)
        return JSONResponse(
            {
                "agent_name": meta.agent_name,
                "url": f"/v1/agent-avatars/{quote(meta.agent_name, safe='')}",
                "updated_at": meta.updated_at,
            }
        )

    @router.delete(
        "/agent-avatars/{agent_name}",
        response_class=Response,
        status_code=204,
        responses={
            204: {"description": "Avatar deleted"},
            400: {"description": "agent_name failed validation"},
            404: {"description": "No avatar stored for that agent"},
        },
    )
    async def delete_agent_avatar(request: Request, agent_name: str) -> Response:
        require_user(request, auth_provider)
        name_error = _validate_agent_name(agent_name)
        if name_error is not None:
            return _invalid_input(name_error)
        if not avatar_store.delete(agent_name):
            return JSONResponse(
                {"error": {"code": "not_found", "message": "no avatar for that agent"}},
                status_code=404,
            )
        return Response(status_code=204)

    return router

"""Authenticated Superset proxy at ``/superset/app``, with a signed identity.

The signing, header and cookie rules are the gateway portal proxy's
(``omnigent.airbrx.gateway.proxy``), and the prefix-independent pieces are
imported from it. One difference: Superset is served under the subpath
itself, so the full path, ``/superset/app`` included, goes upstream and is
what the signature covers.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from omnigent.airbrx.gateway.proxy import (
    _HOP_BY_HOP,
    HEADER_EMAIL,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    MIN_SECRET_BYTES,
    _is_omnigent_cookie,
    _request_headers,
    signature,
    verified_email,
)
from omnigent.server.routes._auth_helpers import get_user_id

_log = logging.getLogger(__name__)

PREFIX = "/superset/app"
SECRET_ENV = "AIRBRX_SUPERSET_IDENTITY_SECRET"
UPSTREAM_ENV = "AIRBRX_SUPERSET_UPSTREAM"
ALLOWED_USERS_ENV = "AIRBRX_SUPERSET_ALLOWED_USERS"
DEFAULT_UPSTREAM = "http://127.0.0.1:8088"

#: Chart and dashboard JSON, a CSV upload. Same cap as the gateway portal.
MAX_BODY_BYTES = 10 * 1024 * 1024
#: A cold serverless warehouse behind a chart has been measured at 15s; a hung
#: upstream must still not hold a request open indefinitely.
TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)

_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

__all__ = ["PREFIX", "create_superset_app_router", "signature"]


def identity_secret() -> bytes | None:
    """The shared key, or None when it is unset or too short to use."""
    secret = os.environ.get(SECRET_ENV, "").encode()
    return secret if len(secret) >= MIN_SECRET_BYTES else None


def upstream_base() -> httpx.URL | None:
    """``AIRBRX_SUPERSET_UPSTREAM`` as an origin, or None when it is not one."""
    raw = os.environ.get(UPSTREAM_ENV, "").strip() or DEFAULT_UPSTREAM
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        return None
    return httpx.URL(f"{parts.scheme}://{parts.netloc}")


def _target(request: Request) -> str:
    """The full path, prefix included, plus the query, exactly as it goes upstream.

    Taken from the raw request target so the bytes signed are the bytes sent.
    Dot segments are refused rather than resolved: the HTTP client would
    collapse them, so ``/superset/app/../x`` would leave the prefix.
    """
    raw_path: bytes | None = request.scope.get("raw_path")
    raw = raw_path.decode("latin-1") if raw_path else request.url.path
    if raw != PREFIX and not raw.startswith(PREFIX + "/"):
        raise HTTPException(400, "Malformed Superset path")
    if any(s in {".", ".."} for s in unquote(raw).split("/")):
        raise HTTPException(400, "Dot segments are not forwarded")
    query = request.scope.get("query_string", b"").decode("latin-1")
    return f"{raw}?{query}" if query else raw


def _scoped_set_cookie(value: str) -> str | None:
    """A ``Set-Cookie`` value as the browser may receive it, or None to drop it.

    A cookie named like Omnigent's own is dropped. Every other cookie is
    scoped to ``/superset/app``, left byte for byte when it already is.
    """
    first, *attributes = value.split(";")
    if _is_omnigent_cookie(first.split("=", 1)[0]):
        return None
    path = None
    for attribute in attributes:
        key, _, val = attribute.partition("=")
        if key.strip().lower() == "path":
            path = val.strip()
    if path is not None and (path == PREFIX or path.startswith(PREFIX + "/")):
        return value
    kept = [a for a in attributes if a.partition("=")[0].strip().lower() != "path"]
    return ";".join([first, *kept, f" Path={PREFIX}"])


def _response_headers(response: httpx.Response) -> list[tuple[bytes, bytes]]:
    connection = {
        t.strip().lower() for t in response.headers.get("connection", "").split(",") if t.strip()
    }
    out: list[tuple[bytes, bytes]] = []
    for name, value in response.headers.raw:
        lower = name.decode("latin-1").lower()
        if lower in _HOP_BY_HOP | connection:
            continue
        if lower == "set-cookie":
            scoped = _scoped_set_cookie(value.decode("latin-1"))
            if scoped is None:
                continue
            value = scoped.encode("latin-1")
        out.append((name, value))
    return out


async def _body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(413, "Request body too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(413, "Request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def create_superset_app_router(*, auth_provider: Any) -> APIRouter:
    """``/superset/app`` and everything under it, forwarded with a signed identity."""
    router = APIRouter()

    async def forward(request: Request) -> StreamingResponse:
        user = get_user_id(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Superset requires Omnigent sign-in")
        email = verified_email(auth_provider, user)
        if email is None:
            raise HTTPException(
                403, "Your Omnigent sign-in does not carry a verified email address"
            )
        allowed = {
            value.strip().lower()
            for value in os.environ.get(ALLOWED_USERS_ENV, "").split(",")
            if value.strip()
        }
        if email not in allowed:
            raise HTTPException(403, "Superset is not assigned to this identity")
        secret = identity_secret()
        base = upstream_base()
        if secret is None or base is None:
            raise HTTPException(503, "Superset is not configured on this server")

        target = _target(request)
        url = base.copy_with(raw_path=target.encode("latin-1"))
        if url.raw_path.decode("latin-1") != target:
            # The client would re-encode the target, and the signature would
            # then cover bytes Superset never receives.
            raise HTTPException(400, "Malformed Superset path")

        # Read the body before signing, so a slow upload does not age the
        # timestamp towards the verifier's window.
        body = await _body(request)
        timestamp = int(time.time())
        headers = _request_headers(request)
        headers += [
            (HEADER_EMAIL, email),
            (HEADER_TIMESTAMP, str(timestamp)),
            (HEADER_SIGNATURE, signature(secret, email, timestamp, request.method, target)),
        ]

        client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=False)
        try:
            upstream = await client.send(
                client.build_request(request.method, url, headers=headers, content=body),
                stream=True,
            )
        except httpx.TimeoutException:
            await client.aclose()
            raise HTTPException(504, "Superset did not answer in time") from None
        except httpx.HTTPError as exc:
            await client.aclose()
            _log.warning("Superset upstream unreachable: %s", type(exc).__name__)
            raise HTTPException(502, "Superset did not answer") from None

        async def close() -> None:
            await upstream.aclose()
            await client.aclose()

        response = StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            background=BackgroundTask(close),
        )
        response.raw_headers = _response_headers(upstream)
        return response

    router.add_api_route(PREFIX, forward, methods=_METHODS, include_in_schema=False)
    router.add_api_route(
        PREFIX + "/{path:path}", forward, methods=_METHODS, include_in_schema=False
    )
    return router

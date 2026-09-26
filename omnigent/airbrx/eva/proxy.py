"""The outreach app, served inside Omnigent at ``/eva/app``, with a signed identity.

Contract v1.1 (docs/eva/WORKSPACE.md, "Identity contract"): Omnigent forwards
``/eva/app/<path>`` to ``OUTREACH_UPSTREAM`` with the prefix stripped, removes
every inbound ``X-Omnigent-*`` header, and sets the signed-in user's email, a
unix timestamp, and an HMAC-SHA256 over ``email, timestamp, METHOD, path``.
The outreach app trusts nothing else, so everything here that decides *who*
the caller is must be server-side: the email comes from Omnigent's own
authenticated identity, never from anything the client sent.

``/mcp`` is never forwarded. Eva reaches it directly with the rep's own bearer
token, and a browser path to it would be a second way in with a weaker key.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from omnigent.server.auth import resolve_auth_header
from omnigent.server.routes._auth_helpers import get_user_id

_log = logging.getLogger(__name__)

PREFIX = "/eva/app"
SECRET_ENV = "OUTREACH_IDENTITY_SECRET"
UPSTREAM_ENV = "OUTREACH_UPSTREAM"
DEFAULT_UPSTREAM = "http://127.0.0.1:8000"
#: The outreach app refuses a shorter key; signing with one here would only
#: produce requests it rejects.
MIN_SECRET_BYTES = 32

HEADER_EMAIL = "X-Omnigent-User-Email"
HEADER_TIMESTAMP = "X-Omnigent-Timestamp"
HEADER_SIGNATURE = "X-Omnigent-Signature"

#: Forms, a CSV of leads. Larger than any page the app posts, small enough
#: that the coordinator never buffers something it should not.
MAX_BODY_BYTES = 10 * 1024 * 1024
#: A cold serverless warehouse behind the app has been measured at 15s for a
#: page; a hung upstream must still not hold a request open indefinitely.
TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)

_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

#: Auth sources whose user id is an email someone verified: the IdP for
#: ``oidc`` (``email_verified``, lowercased at the callback), the trusted proxy
#: for ``header``. ``accounts`` user ids are usernames an admin typed.
_EMAIL_SOURCES = frozenset({"oidc", "header"})
_EMAIL = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")


def signature(secret: bytes, email: str, timestamp: int, method: str, path: str) -> str:
    """Lowercase hex HMAC-SHA256 of ``email\\ntimestamp\\nMETHOD\\npath``."""
    message = f"{email}\n{timestamp}\n{method.upper()}\n{path}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def identity_secret() -> bytes | None:
    """The shared key, or None when it is unset or too short to use."""
    raw = os.environ.get(SECRET_ENV, "")
    secret = raw.encode()
    return secret if len(secret) >= MIN_SECRET_BYTES else None


def upstream_base() -> httpx.URL | None:
    """``OUTREACH_UPSTREAM`` as an origin, or None when it is not one."""
    raw = os.environ.get(UPSTREAM_ENV, "").strip() or DEFAULT_UPSTREAM
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if parts.path not in {"", "/"} or parts.query or parts.fragment:
        return None
    return httpx.URL(f"{parts.scheme}://{parts.netloc}")


def verified_email(auth_provider: Any, user_id: str | None) -> str | None:
    """The caller's email as Omnigent authenticated it, lowercased, or None.

    Only the server-side identity is consulted. A user id from an auth source
    that does not verify email, or one that is not shaped like an address
    (the single-user ``"local"``, a username), yields None.
    """
    if not user_id or getattr(auth_provider, "_source", None) not in _EMAIL_SOURCES:
        return None
    email = user_id.strip().lower()
    return email if len(email) <= 254 and _EMAIL.match(email) else None


def _target(request: Request) -> str:
    """The path after the prefix, including the query, exactly as it goes upstream.

    Taken from the raw request target so the bytes signed are the bytes sent.
    Dot segments are refused rather than resolved: the HTTP client would
    collapse them, so ``/eva/app/x/../mcp`` would arrive as ``/mcp``.
    """
    raw_path: bytes | None = request.scope.get("raw_path")
    raw = raw_path.decode("latin-1") if raw_path else request.url.path
    if raw != PREFIX and not raw.startswith(PREFIX + "/"):
        raise HTTPException(400, "Malformed Eva app path")
    rest = raw[len(PREFIX) :] or "/"
    segments = [s for s in unquote(rest).split("/") if s]
    if any(s in {".", ".."} for s in segments):
        raise HTTPException(400, "Dot segments are not forwarded")
    if segments and segments[0].split(";", 1)[0].lower() == "mcp":
        raise HTTPException(404, "Not found")
    query = request.scope.get("query_string", b"").decode("latin-1")
    return f"{rest}?{query}" if query else rest


def _without_omnigent_cookies(value: str) -> str:
    """Drop Omnigent's own session and sign-in cookies from a Cookie header.

    The outreach app has no use for them, and a session JWT is a credential
    for the whole of Omnigent. Every other cookie passes; a header carrying
    none of Omnigent's passes byte for byte (contract v1.2, CSRF).
    """
    pairs = value.split(";")
    kept = [
        p
        for p in pairs
        if not p.split("=", 1)[0].strip().removeprefix("__Host-").startswith("ap_")
    ]
    if len(kept) == len(pairs):
        return value
    return "; ".join(p.strip() for p in kept if p.strip())


def _request_headers(request: Request) -> list[tuple[str, str]]:
    connection = {
        t.strip().lower() for t in request.headers.get("connection", "").split(",") if t.strip()
    }
    drop = (
        _HOP_BY_HOP
        | connection
        | {"content-length", "authorization", resolve_auth_header().lower()}
    )
    out: list[tuple[str, str]] = []
    for name, value in request.headers.items():
        lower = name.lower()
        if lower in drop or lower.startswith("x-omnigent-"):
            continue
        if lower in {"forwarded", "x-real-ip"} or lower.startswith("x-forwarded-"):
            continue
        if lower == "cookie":
            value = _without_omnigent_cookies(value)
            if not value:
                continue
        out.append((name, value))
    # Contract v1.1. Host is kept as the browser sent it, so the app's url_for
    # builds public URLs. The app's uvicorn trusts X-Forwarded-* from loopback,
    # which is us, so X-Forwarded-For is never set: a forwarded client address
    # would become the app's peer address and defeat its loopback check.
    out.append(("X-Forwarded-Proto", "https"))
    return out


def _response_headers(response: httpx.Response) -> list[tuple[bytes, bytes]]:
    connection = {
        t.strip().lower() for t in response.headers.get("connection", "").split(",") if t.strip()
    }
    return [
        (name, value)
        for name, value in response.headers.raw
        if name.decode("latin-1").lower() not in _HOP_BY_HOP | connection
    ]


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


def create_eva_app_router(*, auth_provider: Any) -> APIRouter:
    """``/eva/app`` and everything under it, forwarded with a signed identity."""
    router = APIRouter()

    async def forward(request: Request) -> StreamingResponse:
        # 401 raised here rather than through require_user's OmnigentError, so
        # the refusal does not depend on an app-level exception handler.
        user = get_user_id(request, auth_provider)
        if user is None:
            raise HTTPException(401, "Eva's app requires Omnigent sign-in")
        email = verified_email(auth_provider, user)
        if email is None:
            raise HTTPException(
                403, "Your Omnigent sign-in does not carry a verified email address"
            )
        secret = identity_secret()
        base = upstream_base()
        if secret is None or base is None:
            raise HTTPException(503, "Eva's app is not configured on this server")

        target = _target(request)
        url = base.copy_with(raw_path=target.encode("latin-1"))
        if url.raw_path.decode("latin-1") != target:
            # The client would re-encode the target, and the signature would
            # then cover bytes the app never receives.
            raise HTTPException(400, "Malformed Eva app path")

        # Read the body before signing, so a slow upload does not age the
        # timestamp towards the app's 60 second window.
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
            raise HTTPException(504, "The outreach app did not answer in time") from None
        except httpx.HTTPError as exc:
            await client.aclose()
            _log.warning("Eva app upstream unreachable: %s", type(exc).__name__)
            raise HTTPException(502, "The outreach app did not answer") from None

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

"""Airbrx Superset at /superset/app: Omnigent's side of the signed identity.

Same contract as the gateway portal proxy, except the full path, prefix
included, goes upstream and is what the signature covers. The upstream is a
recording ``httpx.MockTransport``, so the refusals can show nothing was sent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.airbrx.superset import proxy
from omnigent.airbrx.superset.proxy import create_superset_app_router, signature

SECRET = "test-secret-0123456789abcdef0123456789abcdef"
EMAIL = "aerickson@airbrx.com"
TIMESTAMP = 1790000000


def _resp(status: int, headers: Any = None, body: bytes = b"") -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))


class _Provider:
    def __init__(self, user: str | None, source: str = "oidc") -> None:
        self._user = user
        self._source = source

    def get_user_id(self, request: Any) -> str | None:
        return self._user


def _client(
    monkeypatch: pytest.MonkeyPatch,
    upstream=None,
    *,
    user: str | None = EMAIL,
    source: str = "oidc",
    secret: str | None = SECRET,
) -> tuple[TestClient, list[httpx.Request]]:
    monkeypatch.setenv("AIRBRX_SUPERSET_ALLOWED_USERS", f"someone@else.com, {EMAIL.upper()}")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return upstream(request) if upstream else _resp(200, body=b"ok")

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("omnigent.airbrx.superset.proxy.httpx.AsyncClient", factory)
    monkeypatch.setattr("omnigent.airbrx.superset.proxy.time.time", lambda: TIMESTAMP)
    monkeypatch.delenv("AIRBRX_SUPERSET_UPSTREAM", raising=False)
    if secret is None:
        monkeypatch.delenv("AIRBRX_SUPERSET_IDENTITY_SECRET", raising=False)
    else:
        monkeypatch.setenv("AIRBRX_SUPERSET_IDENTITY_SECRET", secret)

    app = FastAPI()
    app.include_router(create_superset_app_router(auth_provider=_Provider(user, source)))
    return TestClient(app, follow_redirects=False), seen


def _superset_verifies(request: httpx.Request) -> bool:
    """What Superset computes: HMAC over the raw target it receives."""
    h = request.headers
    expected = signature(
        SECRET.encode(),
        h["x-omnigent-user-email"],
        int(h["x-omnigent-timestamp"]),
        request.method,
        request.url.raw_path.decode("latin-1"),
    )
    return h["x-omnigent-signature"] == expected


# --------------------------------------------------------------------------
# The prefix is kept, and signed
# --------------------------------------------------------------------------


def test_the_prefix_and_query_are_forwarded_and_signed(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    assert client.get("/superset/app/superset/welcome/?a=1&b=%2F").status_code == 200
    [sent] = seen
    target = "/superset/app/superset/welcome/?a=1&b=%2F"
    assert sent.url.raw_path.decode("latin-1") == target
    assert str(sent.url).startswith("http://127.0.0.1:8088/superset/app/")
    assert sent.headers["x-omnigent-user-email"] == EMAIL
    assert sent.headers["x-omnigent-timestamp"] == str(TIMESTAMP)
    expected = signature(SECRET.encode(), EMAIL, TIMESTAMP, "GET", target)
    assert sent.headers["x-omnigent-signature"] == expected
    assert _superset_verifies(sent)


def test_the_signature_is_the_documented_hmac(monkeypatch) -> None:
    import hashlib
    import hmac

    client, seen = _client(monkeypatch)
    client.get("/superset/app/api/v1/chart/?q=(page:0)")
    target = "/superset/app/api/v1/chart/?q=(page:0)"
    message = f"{EMAIL}\n{TIMESTAMP}\nGET\n{target}".encode()
    assert (
        seen[0].headers["x-omnigent-signature"]
        == hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
    )


def test_the_bare_prefix_is_forwarded_as_is(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.get("/superset/app")
    client.get("/superset/app/")
    assert [r.url.raw_path for r in seen] == [b"/superset/app", b"/superset/app/"]
    assert all(_superset_verifies(r) for r in seen)


def test_static_assets_under_the_prefix_are_forwarded(monkeypatch) -> None:
    client, seen = _client(
        monkeypatch, lambda r: _resp(200, {"Content-Type": "text/javascript"}, b"x()")
    )
    response = client.get("/superset/app/static/assets/spa.abc123.entry.js")
    assert response.text == "x()"
    assert seen[0].url.raw_path == b"/superset/app/static/assets/spa.abc123.entry.js"


@pytest.mark.parametrize(
    "target",
    [
        "/superset/app/api/v1/chart/?q=O%27Connor",
        "/superset/app/explore/?form_data_key=caf%C3%A9&r=%2F",
        "/superset/app/api/v1/dataset/a%2Fb",
    ],
)
def test_the_signature_covers_the_bytes_received(monkeypatch, target) -> None:
    client, seen = _client(monkeypatch)
    client.get(target)
    [sent] = seen
    assert sent.url.raw_path.decode("latin-1") == target
    assert _superset_verifies(sent)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_json_api_writes_forward_body_and_verify(monkeypatch, method) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(201, {"Content-Type": "application/json"}, b'{"id": 7}')

    client, seen = _client(monkeypatch, upstream)
    payload = {"slice_name": "Hit rate", "viz_type": "table"}
    response = client.request(
        method,
        "/superset/app/api/v1/chart/7?x=1",
        content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-CSRFToken": "tok"},
    )
    assert response.status_code == 201
    assert response.json() == {"id": 7}
    [sent] = seen
    assert sent.method == method
    assert json.loads(sent.content) == payload
    assert sent.headers["content-type"] == "application/json"
    assert sent.headers["x-csrftoken"] == "tok"
    assert _superset_verifies(sent)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_anonymous_gets_401_and_never_reaches_upstream(monkeypatch) -> None:
    client, seen = _client(monkeypatch, user=None)
    for method in ("GET", "POST"):
        response = client.request(
            method, "/superset/app/", headers={"X-Omnigent-User-Email": EMAIL}
        )
        assert response.status_code == 401
    assert seen == []


def test_unassigned_verified_identity_is_denied(monkeypatch) -> None:
    client, seen = _client(monkeypatch, user="other@airbrx.com")
    assert client.get("/superset/app/").status_code == 403
    assert seen == []


@pytest.mark.parametrize(
    ("user", "source"),
    [("local", "header"), (EMAIL, "accounts"), ("not-an-email", "oidc")],
)
def test_an_identity_that_is_not_a_verified_email_is_refused(monkeypatch, user, source) -> None:
    client, seen = _client(monkeypatch, user=user, source=source)
    assert client.get("/superset/app/").status_code == 403
    assert seen == []


@pytest.mark.parametrize("secret", [None, "", "x" * 31])
def test_no_usable_secret_means_503_and_nothing_forwarded(monkeypatch, secret) -> None:
    client, seen = _client(monkeypatch, secret=secret)
    assert client.get("/superset/app/").status_code == 503
    assert seen == []


@pytest.mark.parametrize(
    "path",
    [
        "/superset/app/../v1/sessions",
        "/superset/app/x/%2e%2e/%2e%2e/v1",
        "/superset/app/static/%2E/x",
    ],
)
def test_dot_segments_are_refused(monkeypatch, path) -> None:
    client, seen = _client(monkeypatch)
    for method in ("GET", "POST"):
        assert client.request(method, path).status_code in {400, 404}
    assert seen == []


def test_an_oversized_body_is_refused_before_upstream(monkeypatch) -> None:
    monkeypatch.setattr(proxy, "MAX_BODY_BYTES", 16)
    client, seen = _client(monkeypatch)
    assert client.post("/superset/app/api/v1/x", content=b"x" * 17).status_code == 413
    assert seen == []


def test_an_unreachable_upstream_is_a_502(monkeypatch) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client, _ = _client(monkeypatch, upstream)
    assert client.get("/superset/app/").status_code == 502


def test_the_upstream_is_configurable_and_must_be_an_origin(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    monkeypatch.setenv("AIRBRX_SUPERSET_UPSTREAM", "http://127.0.0.1:9099/")
    client.get("/superset/app/health")
    assert str(seen[0].url) == "http://127.0.0.1:9099/superset/app/health"
    monkeypatch.setenv("AIRBRX_SUPERSET_UPSTREAM", "http://127.0.0.1:8088/superset")
    assert client.get("/superset/app/health").status_code == 503
    assert len(seen) == 1


# --------------------------------------------------------------------------
# Headers and cookies
# --------------------------------------------------------------------------


def test_client_identity_and_forwarding_headers_are_stripped(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.get(
        "/superset/app/",
        headers={
            "Host": "omnigent.airbrx.ai",
            "X-Omnigent-User-Email": "ben@airbrx.com",
            "X-OMNIGENT-SIGNATURE": "0" * 64,
            "X-Omnigent-Anything": "else",
            "Authorization": "Bearer omnigent-session-jwt",
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Host": "evil.example",
            "x-forwarded-proto": "http",
            "Forwarded": "for=127.0.0.1",
            "X-Real-IP": "127.0.0.1",
        },
    )
    [sent] = seen
    assert sent.headers.get_list("host") == ["omnigent.airbrx.ai"]
    assert sent.headers.get_list("x-omnigent-user-email") == [EMAIL]
    assert len(sent.headers.get_list("x-omnigent-signature")) == 1
    assert _superset_verifies(sent)
    assert "x-omnigent-anything" not in sent.headers
    assert "authorization" not in sent.headers
    assert "forwarded" not in sent.headers and "x-real-ip" not in sent.headers
    forwarded = sorted(k for k in sent.headers if k.lower().startswith("x-forwarded-"))
    assert forwarded == ["x-forwarded-proto"]
    assert sent.headers.get_list("x-forwarded-proto") == ["https"]


def test_omnigent_cookies_never_go_upstream(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.get(
        "/superset/app/",
        headers={"Cookie": "session=sup;__Host-ap_session=jwt; csrf=x;ap_auth_state=s"},
    )
    assert seen[0].headers.get_list("cookie") == ["session=sup; csrf=x"]


def test_set_cookie_is_scoped_to_the_prefix(monkeypatch) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(
            200,
            headers=[
                ("Set-Cookie", "__Host-ap_session=forged; Path=/; Secure; HttpOnly"),
                ("Set-Cookie", "ap_session=forged; Path=/"),
                ("Set-Cookie", "session=ok; Path=/superset/app; HttpOnly"),
                ("Set-Cookie", "deep=1; Path=/superset/app/sqllab"),
                ("Set-Cookie", "rooted=1; path=/; HttpOnly; SameSite=Lax"),
                ("Set-Cookie", "bare=1; HttpOnly"),
                ("Set-Cookie", "sneaky=1; Path=/superset/apple"),
            ],
        )

    client, _ = _client(monkeypatch, upstream)
    response = client.get("/superset/app/login/")
    assert response.headers.get_list("set-cookie") == [
        "session=ok; Path=/superset/app; HttpOnly",
        "deep=1; Path=/superset/app/sqllab",
        "rooted=1; HttpOnly; SameSite=Lax; Path=/superset/app",
        "bare=1; HttpOnly; Path=/superset/app",
        "sneaky=1; Path=/superset/app",
    ]


def test_a_redirect_is_returned_not_followed(monkeypatch) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(302, headers={"Location": "/superset/app/superset/welcome/"})

    client, seen = _client(monkeypatch, upstream)
    response = client.get("/superset/app/")
    assert response.status_code == 302
    assert response.headers["location"] == "/superset/app/superset/welcome/"
    assert len(seen) == 1


def test_the_secret_never_appears_in_a_response_or_a_log(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    responses = []
    client, _ = _client(monkeypatch, unreachable)
    responses.append(client.get("/superset/app/"))
    client, _ = _client(monkeypatch)
    responses.append(client.get("/superset/app/"))
    client, _ = _client(monkeypatch, secret=SECRET[:20])
    responses.append(client.get("/superset/app/"))
    for response in responses:
        assert SECRET[:20] not in response.text
        assert all(SECRET[:20] not in v for v in response.headers.values())
    assert SECRET[:20] not in caplog.text

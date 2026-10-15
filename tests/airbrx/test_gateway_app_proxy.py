"""The gateway portal at /gateway/app: contract v1, Omnigent's side.

The upstream is a recording ``httpx.MockTransport``, so every test can say not
only what the browser got back but what the gateway portal would have received,
and, for the refusals, that it received nothing at all.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.airbrx.gateway import proxy
from omnigent.airbrx.gateway.proxy import create_gateway_app_router, signature

# The contract's test vector, which the gateway portal reproduces too.
SECRET = "test-secret-0123456789abcdef0123456789abcdef"
EMAIL = "aerickson@airbrx.com"
TIMESTAMP = 1790000000
VECTOR = "e21566ae5280a70c8f7ba6b55e6c11b2465634d6b101c67ee5a844f4a12ecaad"


def _resp(status: int, headers: Any = None, body: bytes = b"") -> httpx.Response:
    """An upstream response that is still a stream, as a real one is.

    ``httpx.Response(content=...)`` reads itself on construction, which a
    proxy that streams could never see from the network.
    """
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))


class _Provider:
    """Omnigent's auth provider, reduced to what the proxy reads."""

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
    monkeypatch.setenv("AIRBRX_GATEWAY_ALLOWED_USERS", EMAIL)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return upstream(request) if upstream else _resp(200, body=b"ok")

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("omnigent.airbrx.gateway.proxy.httpx.AsyncClient", factory)
    monkeypatch.setattr("omnigent.airbrx.gateway.proxy.time.time", lambda: TIMESTAMP)
    monkeypatch.delenv("AIRBRX_GATEWAY_UPSTREAM", raising=False)
    if secret is None:
        monkeypatch.delenv("AIRBRX_GATEWAY_IDENTITY_SECRET", raising=False)
    else:
        monkeypatch.setenv("AIRBRX_GATEWAY_IDENTITY_SECRET", secret)

    # No OmnigentError handler: the proxy's refusals must not depend on one.
    app = FastAPI()
    app.include_router(create_gateway_app_router(auth_provider=_Provider(user, source)))
    return TestClient(app, follow_redirects=False), seen


# --------------------------------------------------------------------------
# The signature
# --------------------------------------------------------------------------


def test_the_contract_test_vector() -> None:
    assert signature(SECRET.encode(), EMAIL, TIMESTAMP, "GET", "/leads?page=2") == VECTOR


def test_a_forwarded_request_carries_the_test_vector_end_to_end(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    assert client.get("/gateway/app/leads?page=2").status_code == 200
    [sent] = seen
    assert sent.url.raw_path == b"/leads?page=2"
    assert str(sent.url).startswith("http://127.0.0.1:4318/")
    assert sent.headers["x-omnigent-user-email"] == EMAIL
    assert sent.headers["x-omnigent-timestamp"] == str(TIMESTAMP)
    assert sent.headers["x-omnigent-signature"] == VECTOR


def test_the_email_is_lowercased_before_it_is_signed(monkeypatch) -> None:
    client, seen = _client(monkeypatch, user="AErickson@Airbrx.com")
    client.get("/gateway/app/leads?page=2")
    assert seen[0].headers["x-omnigent-user-email"] == EMAIL
    assert seen[0].headers["x-omnigent-signature"] == VECTOR


def test_the_bare_prefix_is_the_app_root(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.get("/gateway/app")
    client.get("/gateway/app/")
    assert [r.url.raw_path for r in seen] == [b"/", b"/"]
    expected = signature(SECRET.encode(), EMAIL, TIMESTAMP, "GET", "/")
    assert all(r.headers["x-omnigent-signature"] == expected for r in seen)


def test_the_signed_path_is_the_raw_path_that_is_sent(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.post("/gateway/app/leads/a%20b?q=%41", data={"x": "1"})
    [sent] = seen
    assert sent.url.raw_path == b"/leads/a%20b?q=%41"
    expected = signature(SECRET.encode(), EMAIL, TIMESTAMP, "POST", "/leads/a%20b?q=%41")
    assert sent.headers["x-omnigent-signature"] == expected


def _outreach_verifies(request: httpx.Request) -> bool:
    """What the gateway portal computes: HMAC over the raw target it receives."""
    h = request.headers
    target = request.url.raw_path.decode("latin-1")
    expected = signature(
        SECRET.encode(),
        h["x-omnigent-user-email"],
        int(h["x-omnigent-timestamp"]),
        request.method,
        target,
    )
    return h["x-omnigent-signature"] == expected


@pytest.mark.parametrize(
    "target",
    [
        "/leads?q=O%27Connor",
        "/leads?q=O%27Connor&sort=-created",
        "/leads/caf%C3%A9?q=a+b&r=%2F",
        "/accounts/a%2Fb",
    ],
)
def test_contract_v1_2_the_signature_covers_the_bytes_received(monkeypatch, target) -> None:
    """v1.2 (a): sign the raw target as forwarded, before any decoding."""
    client, seen = _client(monkeypatch)
    client.get("/gateway/app" + target)
    [sent] = seen
    assert sent.url.raw_path.decode("latin-1") == target
    assert _outreach_verifies(sent)


def test_contract_v1_2_csrf_headers_and_cookies_pass_unchanged(monkeypatch) -> None:
    """v1.2 (c): Cookie, Set-Cookie and X-CSRF-Token are untouched."""

    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(
            200, headers=[("Set-Cookie", "csrftoken=n3w; Path=/gateway/app; SameSite=Strict")]
        )

    client, seen = _client(monkeypatch, upstream)
    cookie = "csrftoken=abc123;outreach_session=s%3D1"
    response = client.post(
        "/gateway/app/leads/42/claim",
        content=b"csrf_token=abc123",
        headers={
            "Cookie": cookie,
            "X-CSRF-Token": "abc123",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    [sent] = seen
    assert sent.headers.get_list("cookie") == [cookie]
    assert sent.headers.get_list("x-csrf-token") == ["abc123"]
    assert _outreach_verifies(sent)
    assert response.headers.get_list("set-cookie") == [
        "csrftoken=n3w; Path=/gateway/app; SameSite=Strict"
    ]


def test_contract_v1_3_omnigent_cookies_never_go_upstream(monkeypatch) -> None:
    """v1.3 (1): ap_* stripped from Cookie, every other cookie byte for byte."""
    client, seen = _client(monkeypatch)
    client.get(
        "/gateway/app/leads",
        headers={
            "Cookie": "a=1;__Host-ap_session=jwt; csrftoken=x%3By;ap_auth_state=s;__Secure-ap_x=1"
        },
    )
    assert seen[0].headers.get_list("cookie") == ["a=1; csrftoken=x%3By"]
    client.get("/gateway/app/leads", headers={"Cookie": "__Host-ap_session=jwt"})
    assert "cookie" not in seen[1].headers


def test_contract_v1_3_upstream_cannot_set_omnigent_cookies(monkeypatch) -> None:
    """v1.3 (2): an ap_* Set-Cookie is dropped; others are scoped to /gateway/app."""

    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(
            200,
            headers=[
                ("Set-Cookie", "__Host-ap_session=forged; Path=/; Secure; HttpOnly"),
                ("Set-Cookie", "ap_session=forged; Path=/"),
                ("Set-Cookie", "__Secure-ap_auth_state=x; Path=/; Secure"),
                ("Set-Cookie", "csrftoken=ok; Path=/gateway/app; SameSite=Strict"),
                ("Set-Cookie", "deep=1; Path=/gateway/app/leads"),
                ("Set-Cookie", "rooted=1; path=/; HttpOnly"),
                ("Set-Cookie", "bare=1; HttpOnly"),
                ("Set-Cookie", "sneaky=1; Path=/gateway/apple"),
            ],
        )

    client, _ = _client(monkeypatch, upstream)
    response = client.get("/gateway/app/leads")
    assert response.headers.get_list("set-cookie") == [
        "csrftoken=ok; Path=/gateway/app; SameSite=Strict",
        "deep=1; Path=/gateway/app/leads",
        "rooted=1; HttpOnly; Path=/gateway/app",
        "bare=1; HttpOnly; Path=/gateway/app",
        "sneaky=1; Path=/gateway/app",
    ]


# --------------------------------------------------------------------------
# Identity comes from Omnigent, never from the client
# --------------------------------------------------------------------------


def test_a_spoofed_identity_header_is_stripped_and_replaced(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.get(
        "/gateway/app/leads?page=2",
        headers={
            "X-Omnigent-User-Email": "ben@airbrx.com",
            "x-omnigent-timestamp": "1",
            "X-OMNIGENT-SIGNATURE": "0" * 64,
            "X-Omnigent-Anything": "else",
        },
    )
    [sent] = seen
    assert sent.headers.get_list("x-omnigent-user-email") == [EMAIL]
    assert sent.headers.get_list("x-omnigent-timestamp") == [str(TIMESTAMP)]
    assert sent.headers.get_list("x-omnigent-signature") == [VECTOR]
    assert "x-omnigent-anything" not in sent.headers


def test_unauthenticated_gets_401_and_never_reaches_upstream(monkeypatch) -> None:
    client, seen = _client(monkeypatch, user=None)
    for method in ("GET", "POST"):
        response = client.request(
            method, "/gateway/app/leads", headers={"X-Omnigent-User-Email": EMAIL}
        )
        assert response.status_code == 401
    assert seen == []


@pytest.mark.parametrize(
    ("user", "source"),
    [("local", "header"), (EMAIL, "accounts"), ("not-an-email", "oidc"), ("a@b@c.com", "oidc")],
)
def test_an_identity_that_is_not_a_verified_email_is_refused(monkeypatch, user, source) -> None:
    client, seen = _client(monkeypatch, user=user, source=source)
    assert client.get("/gateway/app/leads").status_code == 403
    assert seen == []


def test_omnigent_credentials_are_not_forwarded(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.get(
        "/gateway/app/leads",
        headers={
            "Authorization": "Bearer omnigent-session-jwt",
            "Cookie": "__Host-ap_session=jwt; ap_auth_state=s; outreach_csrf=abc; theme=dark",
            "X-Forwarded-Email": "ben@airbrx.com",
        },
    )
    [sent] = seen
    assert "authorization" not in sent.headers
    assert sent.headers["cookie"] == "outreach_csrf=abc; theme=dark"
    assert "x-forwarded-email" not in sent.headers


def test_a_client_x_forwarded_for_never_reaches_upstream(monkeypatch) -> None:
    """Contract v1.1: the app's uvicorn trusts X-Forwarded-* from loopback, which
    is this proxy, so a forwarded client address would defeat its loopback check."""
    client, seen = _client(monkeypatch)
    client.get(
        "/gateway/app/leads",
        headers={
            "X-Forwarded-For": "127.0.0.1",
            "x-forwarded-proto": "http",
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Port": "80",
            "Forwarded": "for=127.0.0.1",
            "X-Real-IP": "127.0.0.1",
        },
    )
    [sent] = seen
    assert "x-forwarded-for" not in sent.headers
    assert "forwarded" not in sent.headers and "x-real-ip" not in sent.headers
    forwarded = sorted(k for k in sent.headers if k.lower().startswith("x-forwarded-"))
    assert forwarded == ["x-forwarded-proto"]
    assert sent.headers.get_list("x-forwarded-proto") == ["https"]


def test_the_host_header_is_preserved(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    client.get("/gateway/app/leads", headers={"Host": "omnigent.airbrx.ai"})
    assert seen[0].headers.get_list("host") == ["omnigent.airbrx.ai"]


# --------------------------------------------------------------------------
# /mcp is never forwarded
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/gateway/app/mcp",
        "/gateway/app/mcp/",
        "/gateway/app/mcp/tools",
        "/gateway/app/MCP",
        "/gateway/app/%6Dcp/",
        "/gateway/app//mcp",
        "/gateway/app/%2Fmcp",
        "/gateway/app/x/../mcp",
        "/gateway/app/x/%2e%2e/mcp",
    ],
)
def test_mcp_is_not_forwarded(monkeypatch, path) -> None:
    client, seen = _client(monkeypatch)
    for method in ("GET", "POST"):
        assert client.request(method, path).status_code in {400, 404}
    assert seen == []


def test_a_path_that_merely_starts_with_mcp_is_forwarded(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    assert client.get("/gateway/app/mcp-help").status_code == 200
    assert seen[0].url.raw_path == b"/mcp-help"


# --------------------------------------------------------------------------
# What passes through, and what does not
# --------------------------------------------------------------------------


def test_a_post_body_and_set_cookie_pass_through(monkeypatch) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(
            200,
            headers=[
                ("Set-Cookie", "outreach_csrf=new; Path=/gateway/app; HttpOnly"),
                ("Set-Cookie", "flash=saved; Path=/gateway/app"),
                ("Content-Type", "text/html"),
            ],
            body=b"<p>saved</p>",
        )

    client, seen = _client(monkeypatch, upstream)
    response = client.post(
        "/gateway/app/leads/42/claim",
        content=b"csrf_token=t0k3n&note=hello+world",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    [sent] = seen
    assert sent.content == b"csrf_token=t0k3n&note=hello+world"
    assert sent.headers["content-type"] == "application/x-www-form-urlencoded"
    assert response.status_code == 200
    assert response.text == "<p>saved</p>"
    assert response.headers.get_list("set-cookie") == [
        "outreach_csrf=new; Path=/gateway/app; HttpOnly",
        "flash=saved; Path=/gateway/app",
    ]


def test_a_redirect_is_returned_not_followed(monkeypatch) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(303, headers={"Location": "/gateway/app/leads/42"})

    client, seen = _client(monkeypatch, upstream)
    response = client.post("/gateway/app/leads/42/claim", data={"csrf_token": "t"})
    assert response.status_code == 303
    assert response.headers["location"] == "/gateway/app/leads/42"
    assert len(seen) == 1


@pytest.mark.parametrize("status", [403, 404, 422, 500])
def test_status_codes_are_kept(monkeypatch, status) -> None:
    client, _ = _client(monkeypatch, lambda r: _resp(status, body=b"from outreach"))
    response = client.get("/gateway/app/leads")
    assert (response.status_code, response.text) == (status, "from outreach")


def test_hop_by_hop_headers_are_stripped_both_ways(monkeypatch) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        return _resp(200, headers={"Connection": "X-Private", "X-Private": "1", "Keep-Alive": "5"})

    client, seen = _client(monkeypatch, upstream)
    response = client.get(
        "/gateway/app/leads", headers={"Connection": "X-Secret", "X-Secret": "1", "TE": "trailers"}
    )
    assert "x-secret" not in seen[0].headers and "te" not in seen[0].headers
    assert "x-private" not in response.headers and "keep-alive" not in response.headers


def test_an_oversized_body_is_refused_before_upstream(monkeypatch) -> None:
    monkeypatch.setattr(proxy, "MAX_BODY_BYTES", 16)
    client, seen = _client(monkeypatch)
    assert client.post("/gateway/app/import", content=b"x" * 17).status_code == 413
    assert seen == []


def test_an_unreachable_upstream_is_a_502(monkeypatch) -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client, _ = _client(monkeypatch, upstream)
    assert client.get("/gateway/app/leads").status_code == 502


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize("secret", [None, "", "x" * 31])
def test_no_usable_secret_means_503_and_nothing_forwarded(monkeypatch, secret) -> None:
    client, seen = _client(monkeypatch, secret=secret)
    assert client.get("/gateway/app/leads").status_code == 503
    assert seen == []


def test_the_upstream_is_configurable(monkeypatch) -> None:
    client, seen = _client(monkeypatch)
    monkeypatch.setenv("AIRBRX_GATEWAY_UPSTREAM", "http://127.0.0.1:8123/")
    client.get("/gateway/app/leads")
    assert str(seen[0].url) == "http://127.0.0.1:8123/leads"


@pytest.mark.parametrize("upstream", ["ftp://x", "http://127.0.0.1:4318/prefix", "nonsense"])
def test_an_upstream_that_is_not_an_origin_is_refused(monkeypatch, upstream) -> None:
    client, seen = _client(monkeypatch)
    monkeypatch.setenv("AIRBRX_GATEWAY_UPSTREAM", upstream)
    assert client.get("/gateway/app/leads").status_code == 503
    assert seen == []


def test_the_secret_never_appears_in_a_response_or_a_log(monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    responses = []
    client, _ = _client(monkeypatch, unreachable)
    responses.append(client.get("/gateway/app/leads"))
    responses.append(client.get("/gateway/app/mcp"))
    client, _ = _client(monkeypatch)
    responses.append(client.get("/gateway/app/leads"))
    client, _ = _client(monkeypatch, secret=SECRET[:20])
    responses.append(client.get("/gateway/app/leads"))
    for response in responses:
        assert SECRET[:20] not in response.text
        assert all(SECRET[:20] not in v for v in response.headers.values())
    assert SECRET[:20] not in caplog.text


def test_unassigned_verified_identity_is_denied(monkeypatch) -> None:
    client, seen = _client(monkeypatch, user="other@airbrx.com")
    assert client.get("/gateway/app/").status_code == 403
    assert not seen

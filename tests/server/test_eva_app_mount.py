"""/eva/app is mounted on the real server ahead of the web UI's catch-all."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/eva/app", "/eva/app/", "/eva/app/leads?page=2"])
async def test_eva_app_is_the_proxy_not_the_spa(client: httpx.AsyncClient, path: str) -> None:
    # No auth provider means no one to vouch for the caller: 401, never forwarded.
    response = await client.get(path)
    assert response.status_code == 401
    assert "text/html" not in response.headers.get("content-type", "")

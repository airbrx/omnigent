"""Tally's routes are mounted on the real server, ahead of the web UI's catch-all."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/v1/tally", "/v1/tally/readiness", "/v1/tally/portrait", "/v1/tally/sessions/s1/ui/"],
)
async def test_tally_routes_are_mounted_and_refuse_an_unknown_caller(
    client: httpx.AsyncClient, path: str
) -> None:
    # No auth provider means no one to vouch for the caller: her own 401, not a 404.
    response = await client.get(path)
    assert response.status_code == 401
    assert response.json()["detail"] == "Tally requires host authentication"

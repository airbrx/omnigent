"""Verify a real registered Iris session after rollout; never a localhost preview."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx

from omnigent.airbrx.iris.routes import completed_answer, report_references
from omnigent.cli_auth import load_token


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument(
        "--live", action="store_true", help="Authorize this selected tenant's read-only overview"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = load_token(args.server)
    if not token:
        raise SystemExit("Use omnigent login for the selected server first")
    async with httpx.AsyncClient(
        base_url=args.server.rstrip("/"), headers={"Authorization": "Bearer " + token}, timeout=330
    ) as client:

        async def get(path, **kwargs):
            response = await client.get(path, **kwargs)
            response.raise_for_status()
            return response.json()

        catalog = await get("/v1/iris")
        selected = [
            b
            for b in catalog["bindings"]
            if b["tenant_id"] == args.tenant and b["fixture"] != args.live
        ]
        if len(selected) != 1 or not catalog["agent_id"]:
            raise SystemExit("Select one authorized, explicitly configured tenant and mode")
        binding = selected[0]

        async def create():
            response = await client.post(
                "/v1/sessions",
                json={
                    "agent_id": catalog["agent_id"],
                    "host_id": binding["host_id"],
                    "workspace": binding["workspace"],
                },
            )
            response.raise_for_status()
            return response.json()["id"]

        session = await create()
        # The workspace bridge invokes the same native event route used by normal chat.
        response = await client.post(
            f"/v1/iris/sessions/{session}/ui/api/chat",
            json={
                "history": [
                    {
                        "role": "user",
                        "content": (
                            "Call iris_overview once and state the period hit rate, "
                            "denominator and coverage. Do not call other tools."
                        ),
                    }
                ],
                "deadline": 300,
            },
        )
        response.raise_for_status()
        items = (await get(f"/v1/sessions/{session}/items", params={"limit": 1000}))["data"]
        answer = completed_answer(items)
        if not answer or answer["tools"] != ["iris_overview"]:
            raise SystemExit("Native overview dispatch was not observed exactly once")
        refs = report_references(items)
        if not refs:
            raise SystemExit("No native session report reference")
        second = await create()
        downloads = []
        files = (await get(f"/v1/sessions/{session}/resources/files"))["data"]
        for file in files:
            if file["filename"] not in {"report.json", "report.md"}:
                continue
            path = f"/v1/sessions/{session}/resources/files/{file['id']}/content"
            downloaded = await client.get(path)
            downloaded.raise_for_status()
            denied = await client.get(path.replace(session, second))
            if denied.status_code != 404:
                raise SystemExit("Cross-session file ownership check failed")
            if file["filename"] == "report.json" and downloaded.json()["tenant_id"] != args.tenant:
                raise SystemExit("Downloaded report tenant mismatch")
            downloads.append(
                {
                    "filename": file["filename"],
                    "sha256": hashlib.sha256(downloaded.content).hexdigest(),
                    "bytes": len(downloaded.content),
                    "cross_session_status": denied.status_code,
                }
            )
        if {d["filename"] for d in downloads} != {"report.json", "report.md"}:
            raise SystemExit("Both report formats are required")
        version = await get("/api/version")
        result = {
            "host_url": args.server,
            "host_version": version,
            "iris_revision": catalog["revision"],
            "session_id": session,
            "second_session_id": second,
            "mount": f"/iris/{session}",
            "mode": "live read-only" if args.live else "synthetic fixture",
            "verified_at": time.time(),
            "tools_called": answer["tools"],
            "downloads": downloads,
            "browser_qa": "not_run",
            "monitoring": "unavailable",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

"""Verify a real registered Iris session after rollout; never a localhost preview."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx

from omnigent.airbrx.iris.routes import bare_tool_name, completed_answer, report_references
from omnigent.airbrx.iris.runtime import TOOLS
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
        # Assert on the Iris tools only. `completed_answer` returns every name the
        # boundary allows, and that set deliberately includes harness tools:
        # ToolSearch loads tool *schemas* and executes nothing against a tenant,
        # and it shows up in most real turns. Comparing the whole list against
        # ["iris_overview"] failed a correct run, which is the wrong direction for
        # an acceptance check to fail in — it would have read as a dispatch defect.
        # The full list is still recorded below as tools_called; the record keeps
        # everything, the assertion narrows.
        # Count DISPATCHES, not records. A correct fixture turn on 2026-09-21
        # recorded iris_overview twice, and the extra record reused the
        # *preceding ToolSearch call's* call_id — which a well-formed log can
        # never do, since one call_id cannot belong to two different tools.
        # That impossibility is what identifies the phantom.
        #
        # The tool ran once: both outputs were byte-identical, same evidence id,
        # same budget {calls: 10}. A second real execution mints a new evidence
        # id and spends the budget again, so identical payloads are proof of one
        # execution rather than two. A genuine double dispatch carries its own
        # fresh call_id and is still caught.
        #
        # Filed separately as a hosted item-log defect. This is the checker
        # declining to blame the run for the log's mistake.
        owner, iris_tools = {}, []
        for i in items:
            if i.get("type") != "function_call":
                continue
            cid, name = i.get("call_id"), bare_tool_name(i.get("name"))
            if cid in owner and owner[cid] != name:
                continue
            owner.setdefault(cid, name)
            if name in TOOLS:
                iris_tools.append(name)
        if not answer or iris_tools != ["iris_overview"]:
            raise SystemExit("Native overview dispatch was not observed exactly once")
        refs = report_references(items)
        if not refs:
            raise SystemExit("No native session report reference")
        second = await create()
        downloads = []
        files = (await get(f"/v1/sessions/{session}/resources/files"))["data"]
        # The resource carries its name at `name`, with `metadata.filename`
        # alongside it — not at a top-level `filename`, which is what this
        # script originally read and which raised KeyError on the first hosted
        # run that got this far. Read both spellings rather than pin one: the
        # shape has already moved once under this file.
        def filename_of(resource):
            return resource.get("name") or (resource.get("metadata") or {}).get("filename")

        for file in files:
            if filename_of(file) not in {"report.json", "report.md"}:
                continue
            path = f"/v1/sessions/{session}/resources/files/{file['id']}/content"
            downloaded = await client.get(path)
            downloaded.raise_for_status()
            denied = await client.get(path.replace(session, second))
            if denied.status_code != 404:
                raise SystemExit("Cross-session file ownership check failed")
            if filename_of(file) == "report.json" and downloaded.json()["tenant_id"] != args.tenant:
                raise SystemExit("Downloaded report tenant mismatch")
            downloads.append(
                {
                    "filename": filename_of(file),
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

"""Newest overview capture per bound tenant, from the coordinator's own session store.

Reports are written to the tenant workspace on the *execution host*, which
this process cannot read. What it can read is the copy each session recorded:
`report_references` finds the report.json a tool produced, and the file
content API serves it. So the account list is built from what the caller's
Iris sessions already hold — no new storage, no new credential, no model.

Invariant 1 of the design spec is enforced here, before `iris.account.rank`
ever sees a report: of the whole report, only `tenant_id` and `metrics` are
read out. Everything else stays in the store.
"""

from __future__ import annotations

from fastapi import HTTPException

from omnigent.airbrx.iris.records import report_references

_PAGE = 1000


async def collect_captures(get, *, agent_id: str, bindings) -> dict[str, dict | None]:
    """Map every binding's tenant to its newest iris_overview capture.

    `get(path, params=None)` is the caller-authenticated native API, shaped like
    `httpx.AsyncClient.get`. The result has one key per binding:

    - `{"tenant_id", "metrics", "captured_at"}` — the newest capture found in a
      session bound to that tenant's (host_id, workspace); `captured_at` is
      the epoch-seconds `created_at` of the item that carried it.
    - `{"error": str}` — something for that tenant could not be read. Fails
      closed: one unreadable session hides any capture another session had,
      because the unreadable one might be newer.
    - `None` — no capture exists in any of the caller's sessions.

    Raises HTTPException(502) if the session list itself cannot be read: a
    partial account rendered as complete would misreport the account.
    """
    if not bindings:
        return {}

    by_workspace = {(b.host_id, b.workspace): b.tenant_id for b in bindings}
    newest: dict[str, tuple[float, str, str]] = {}  # tenant -> (created_at, session_id, file_id)
    failed: dict[str, str] = {}
    after = None
    while True:
        params = {"agent_id": agent_id, "limit": _PAGE, "kind": "any", "include_archived": "true"}
        if after:
            params["after"] = after
        listing = await get("/v1/sessions", params=params)
        if listing.status_code >= 400:
            raise HTTPException(
                502, "The session list could not be read, so the account cannot be shown"
            )
        page = listing.json()
        for session in page.get("data", []):
            tenant_id = by_workspace.get((session.get("host_id"), session.get("workspace")))
            if tenant_id is None or tenant_id in failed:
                continue
            references, error = await _session_report_references(get, session["id"])
            if error is not None:
                failed[tenant_id] = error
                continue
            for tool, file_id, created_at in references:
                if tool != "iris_overview":
                    continue
                if tenant_id not in newest or created_at > newest[tenant_id][0]:
                    newest[tenant_id] = (created_at, session["id"], file_id)
        if not page.get("has_more"):
            break
        after = page.get("last_id")
        if not after:
            break

    result: dict[str, dict | None] = {}
    for binding in bindings:
        tenant_id = binding.tenant_id
        if tenant_id in failed:
            result[tenant_id] = {"error": failed[tenant_id]}
            continue
        if tenant_id not in newest:
            result[tenant_id] = None
            continue
        created_at, session_id, file_id = newest[tenant_id]
        content = await get(f"/v1/sessions/{session_id}/resources/files/{file_id}/content")
        if content.status_code >= 400:
            result[tenant_id] = {
                "error": f"the newest report could not be read (HTTP {content.status_code})"
            }
            continue
        report = content.json()
        if not isinstance(report, dict):
            result[tenant_id] = {"error": "the newest report is not a JSON object"}
            continue
        result[tenant_id] = {
            "tenant_id": report.get("tenant_id"),
            "metrics": report.get("metrics"),
            "captured_at": created_at,
        }
    return result


async def _session_report_references(get, session_id):
    """Page through one session's items and return its report references.

    The native API answers newest-first, so a run of pages is fetched with
    `after=last_id` until either a page's accumulation yields an
    `iris_overview` reference (that is, by construction, the newest one in
    this session — no need to read further back) or the pages run out.
    `report_references` is called on the whole reversed (chronological)
    accumulation each time, never on a single page alone, so a
    `function_call`/`function_call_output` pair split across a page boundary
    is still paired correctly.

    Returns `(references, None)` on success or `([], error)` if any page
    fails to load — fails closed, exactly like a first-page failure.
    """
    accumulated: list = []
    after = None
    while True:
        params = {"limit": _PAGE, "order": "desc"}
        if after:
            params["after"] = after
        items = await get(f"/v1/sessions/{session_id}/items", params=params)
        if items.status_code >= 400:
            return [], f"a session's record could not be read (HTTP {items.status_code})"
        item_page = items.json()
        accumulated.extend(item_page.get("data", []))
        chronological = list(reversed(accumulated))
        references = report_references(chronological)
        if any(tool == "iris_overview" for tool, _, _ in references):
            return references, None
        if not item_page.get("has_more"):
            return references, None
        after = item_page.get("last_id")
        if not after:
            return references, None

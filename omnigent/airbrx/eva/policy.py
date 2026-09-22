"""Eva's tool boundary. Defence in depth for hosts that auto-register tools.

Self-contained, unlike ``airbrx/iris/policy.py``, which is a thin re-export of a
boundary living inside a vendored package. Eva vendors nothing, so the list is
here, and here is the only place it is written down inside Omnigent.

**This is the second of three refusals, not the only one.** The bundle's
``tools.outreach.tools`` allow list is the first, and the outreach app's own tool
layer is the third and the authoritative one. A boundary that is the sole thing
standing between an agent and a write it should not make is a boundary one
import error away from being nothing, which is exactly the failure
``airbrx/omnigent#42`` recorded against Iris.
"""

from __future__ import annotations

from typing import Any

#: The 17 tools of ``contracts/mcp_tools.md``, minus the two Eva must not hold.
#: Kept as one frozenset rather than "all seventeen minus a deny list" so that a
#: tool added to the contract is absent here until somebody decides it belongs,
#: rather than arriving allowed by default.
EVA_TOOLS = frozenset(
    {
        "list_pool",
        "list_my_leads",
        "get_lead",
        "claim_lead",
        "release_lead",
        "add_lead",
        "update_lead_fields",
        "save_qualification",
        "submit_draft",
        "add_comment",
        "request_approval",
        "log_touch",
        "list_guardrails",
        "query_analytics",
        "record_agent_run",
    }
)

#: Named rather than merely absent, so the denial can say *why* instead of
#: "not in the list", and so that deleting a line here is a visible decision.
#:
#: ``approve_draft``  approval is the lead owner's or an admin's and never the
#:                    author's. Eva writes drafts.
#: ``mark_sent``      nothing in this system sends. A rep sends from their own
#:                    client and then records it. Eva cannot observe that event.
WITHHELD = {
    "approve_draft": "approval belongs to the lead owner or an admin, never the draft's author",
    "mark_sent": "nothing in this system sends; a rep records their own send",
}

#: Discovery, not execution. Same carve-out and the same reason as Iris's:
#: on a hosted turn the model calls this to locate ``mcp__omnigent__get_lead``
#: before calling it, and a boundary that denies the agent's way of finding its
#: own tools gets switched off, which is worse than one that is slightly wider.
DISCOVERY_TOOLS = frozenset({"ToolSearch"})

_MCP_PREFIX = "mcp__omnigent__"


def tool_boundary(event: Any) -> Any:
    """Evaluate one TOOL_CALL event.

    Fail-closed by construction: every path that is not an explicit ALLOW
    returns DENY, and the runner's resolver treats a raise as DENY too.
    """
    if event.get("type") != "tool_call":
        return {"result": "ALLOW", "reason": "Not a tool execution"}

    name = event.get("target", "") or ""
    if name.startswith(_MCP_PREFIX):
        name = name[len(_MCP_PREFIX) :]
    # Some servers namespace by the spec's server key rather than the transport.
    if name.startswith("outreach__"):
        name = name[len("outreach__") :]

    if name in WITHHELD:
        return {"result": "DENY", "reason": f"Eva may not call {name}: {WITHHELD[name]}"}
    if name in EVA_TOOLS:
        return {"result": "ALLOW", "reason": "Registered outreach tool"}
    if name in DISCOVERY_TOOLS:
        return {"result": "ALLOW", "reason": "Read-only tool discovery, executes nothing"}
    return {
        "result": "DENY",
        "reason": "Eva permits only the outreach MCP tools named in her spec",
    }

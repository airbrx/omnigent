"""Tally's tool boundary. Defence in depth for hosts that auto-register tools.

Self-contained, like ``airbrx/eva/policy.py``. v1 of Tally is read-only by
Abram's decision: four portal reads and nothing else. There is no write tool to
carve out, so every write-like name is denied, and the ones worth naming are
named with the reason.

**This is the second of three refusals.** The bundle's ``tools.portal.tools``
allow list is the first; the portal's own MCP layer, which exposes only these
reads to her token, is the third and authoritative one.
"""

from __future__ import annotations

import json
from typing import Any

#: The portal's read tools, the whole of v1. A tool the portal adds later is
#: absent here until somebody decides it belongs.
TALLY_TOOLS = frozenset(
    {
        "get_analytics_overview",
        "get_agent_policy",
        "get_sprint_board",
        "get_health",
    }
)

#: The only argument names each tool may carry, and for ``get_agent_policy``
#: the only values ``agent`` may take. The portal validates too; this refuses
#: a malformed call before it leaves the runner.
TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    "get_analytics_overview": frozenset(),
    "get_agent_policy": frozenset({"agent"}),
    "get_sprint_board": frozenset(),
    "get_health": frozenset(),
}
POLICY_AGENTS = frozenset({"iris", "eva"})

#: Write-like tools named rather than merely absent, so a denial says why.
#: v1 has no write path at all; each of these needs a separately approved tool
#: boundary before Tally may hold it.
_READ_ONLY = "Tally v1 is read-only; a write needs a separately approved tool boundary"
WITHHELD = {
    "save_policy": f"{_READ_ONLY} (agent policy changes belong to a person)",
    "publish_config_revision": f"{_READ_ONLY} (publishing config changes live gateway behaviour)",
    "update_tenant_config": f"{_READ_ONLY} (tenant config changes live gateway behaviour)",
    "update_tenant_rules": f"{_READ_ONLY} (gateway rules change live caching)",
    "update_dashboard": f"{_READ_ONLY} (dashboards and Superset are not hers to edit)",
    "update_route": f"{_READ_ONLY} (routes change where traffic goes)",
}

#: Discovery, not execution, with Eva's reasoning: the claude-sdk harness keeps
#: ``ToolSearch`` in the CLI's base set and the model calls it to locate
#: ``mcp__omnigent__portal__get_health`` before calling it. It returns
#: references and executes nothing. A boundary that denies the agent's way of
#: finding its own tools gets switched off.
DISCOVERY_TOOLS = frozenset({"ToolSearch"})

_MCP_PREFIX = "mcp__omnigent__"

#: The key of the portal server in her bundle's ``tools:`` block. The runner
#: namespaces its tools as ``portal__<tool>``.
PORTAL_SERVER = "portal"


def _arguments(event: Any) -> dict[str, Any] | None:
    """The call's arguments, or ``None`` when they cannot be read.

    Both shapes Eva's policy reads: the engine's ``{"name", "arguments"}`` and
    the inner stack's ``{"tool", "args"}``.
    """
    data = event.get("data")
    if data is None:
        return {}
    if not isinstance(data, dict):
        return None
    args = data.get("arguments", data.get("args"))
    if args is None:
        return {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return None
    return args if isinstance(args, dict) else None


def bare_name(target: str | None) -> str:
    """Strip only the omnigent transport prefix and her own server's prefix.

    ``mcp__claude_ai_Gmail__send_message`` keeps its name, so another server's
    tool can never become one of hers by stripping.
    """
    name = target or ""
    if name.startswith(_MCP_PREFIX):
        name = name[len(_MCP_PREFIX) :]
    if name.startswith(f"{PORTAL_SERVER}__"):
        name = name[len(PORTAL_SERVER) + 2 :]
    return name


def tool_boundary(event: Any) -> Any:
    """Evaluate one TOOL_CALL event.

    Fail-closed by construction: every path that is not an explicit ALLOW
    returns DENY, and the runner's resolver treats a raise as DENY too.
    """
    if event.get("type") != "tool_call":
        return {"result": "ALLOW", "reason": "Not a tool execution"}

    name = bare_name(event.get("target", "") or "")

    if name in WITHHELD:
        return {"result": "DENY", "reason": f"Tally may not call {name}: {WITHHELD[name]}"}
    if name in TALLY_TOOLS:
        args = _arguments(event)
        if args is None:
            return {"result": "DENY", "reason": f"Could not read the arguments of {name}"}
        extra = set(args) - TOOL_ARGUMENTS[name]
        if extra:
            return {"result": "DENY", "reason": f"{name} may not carry {sorted(extra)}"}
        if name == "get_agent_policy" and args.get("agent") not in POLICY_AGENTS:
            return {
                "result": "DENY",
                "reason": f"get_agent_policy reads only {sorted(POLICY_AGENTS)}",
            }
        return {"result": "ALLOW", "reason": "Read-only portal tool"}
    if name in DISCOVERY_TOOLS:
        return {"result": "ALLOW", "reason": "Read-only tool discovery, executes nothing"}
    return {
        "result": "DENY",
        "reason": "Tally v1 is read-only and permits only the four portal reads in her spec",
    }

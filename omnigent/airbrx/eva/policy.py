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

import json
from typing import Any
from urllib.parse import unquote

#: The tools of ``contracts/mcp_tools.md`` Eva may hold. The contract's other
#: tools are either named in ``WITHHELD`` below or simply absent.
#: Kept as one frozenset rather than "all of them minus a deny list" so that a
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
        # The Scoreboard, LinkedIn and GTM plan pages (2026-09-26). Eva reads
        # posts and plan items, and records a post's metrics snapshot or a
        # plan item's actual, both attributed to the rep like every write.
        "list_linkedin_posts",
        "record_linkedin_metrics",
        "list_plan_items",
        "record_plan_actual",
        # airbrx-outreach#149 (2026-09-27): record the address of a post she
        # found on its author's recent-activity page, so its analytics can be
        # opened. The server fills only an empty ``linkedin_url`` and accepts
        # only one post's https://www.linkedin.com/ address, so this can point
        # a post at its page and cannot rewrite anything about it.
        "set_linkedin_post_url",
    }
)

#: Named rather than merely absent, so the denial can say *why* instead of
#: "not in the list", and so that deleting a line here is a visible decision.
#:
#: ``approve_draft``  approval is the lead owner's or an admin's and never the
#:                    author's. Eva writes drafts.
#: ``mark_sent``      nothing in this system sends. A rep sends from their own
#:                    client and then records it. Eva cannot observe that event.
#: ``upsert_linkedin_post``  rewrites a post's title, status, dates or copy.
#:                    Eva only fills an empty address, with ``set_linkedin_post_url``.
WITHHELD = {
    "approve_draft": "approval belongs to the lead owner or an admin, never the draft's author",
    "mark_sent": "nothing in this system sends; a rep records their own send",
    "upsert_linkedin_post": (
        "it rewrites a post's title, status, dates or copy; "
        "Eva only fills an empty address with set_linkedin_post_url"
    ),
}

#: Discovery, not execution. Same carve-out and the same reason as Iris's:
#: on a hosted turn the model calls this to locate ``mcp__omnigent__get_lead``
#: before calling it, and a boundary that denies the agent's way of finding its
#: own tools gets switched off, which is worse than one that is slightly wider.
DISCOVERY_TOOLS = frozenset({"ToolSearch"})

_MCP_PREFIX = "mcp__omnigent__"

#: The key of the Playwright MCP server in her bundle's ``tools:`` block. The
#: runner namespaces its tools as ``browser__<tool>``, so that prefix is the
#: only way a browser tool reaches her. A bare ``browser_navigate`` is not
#: hers: nothing in her spec serves one under that name.
BROWSER_SERVER = "browser"

#: The browser tools Eva may call, each with the only argument names it may
#: carry. Read-only by selection: open a linkedin.com page, read it, wait for
#: it, go back, close. No click, type, key press, form fill, evaluate, run
#: code, upload, screenshot, tab, cookie or network tool, so she cannot post,
#: comment, react or message even if a page talked her into trying.
#:
#: ``browser_snapshot`` may not take ``filename``: that writes the page to a
#: file on the host, which is not reading it.
BROWSER_TOOLS: dict[str, frozenset[str]] = {
    "browser_navigate": frozenset({"url"}),
    "browser_snapshot": frozenset({"target", "depth", "boxes"}),
    "browser_wait_for": frozenset({"time", "text", "textGone"}),
    "browser_navigate_back": frozenset(),
    "browser_close": frozenset(),
}

#: Every page she opens starts with exactly this. Abram's rule, 2026-09-26:
#: linkedin.com only, never another site, including a link LinkedIn offers.
LINKEDIN_PREFIX = "https://www.linkedin.com/"

#: LinkedIn's own redirectors: paths on www.linkedin.com whose job is to send
#: the browser somewhere else. Refused by path, whatever they carry.
#:
#: This matters more since 2026-09-27, when her browser became the operator's
#: own signed-in Chrome. Playwright's ``--allowed-origins`` stops a request to
#: another origin, a page script's navigation and a subresource, but NOT a
#: server redirect: measured in extension mode, a navigate to an allowed page
#: answering 302 landed on the other origin and could be snapshotted. With a
#: dedicated profile that was a page with nobody signed in; in the operator's
#: Chrome it would be a page he is signed in to. So a URL that could redirect
#: off-site is refused before it is ever opened.
LINKEDIN_REDIRECTORS = ("redir/", "redir?", "safety/go", "slink", "externalredirect")

#: How many rounds of percent-decoding a URL gets before it is inspected for an
#: embedded address. Three covers double and triple encoding.
_DECODE_ROUNDS = 3

#: Longest wait she may ask for, in seconds. A page that has not rendered in
#: half a minute is a page to report, not to sit on.
MAX_WAIT_SECONDS = 30


def linkedin_url_ok(url: object) -> bool:
    """Is *url* a page on ``https://www.linkedin.com/`` and nothing else?

    The prefix, trailing slash included, fixes the scheme and the host: a port,
    credentials or a lookalike host (``www.linkedin.com.evil.example``) cannot
    follow it. What is left is what a browser rewrites before it navigates:
    whitespace and control characters, which it strips, and a backslash, which
    it reads as a slash. Any of those is a refusal.

    Then the ways a linkedin.com address can still end somewhere else, because
    a server redirect is the one thing the browser-side fence does not stop:
    one of LinkedIn's redirector paths (:data:`LINKEDIN_REDIRECTORS`), or any
    other address carried inside this one (a ``//`` after the host, plainly or
    percent-encoded, as in ``?url=https%3A%2F%2Fevil.example``). None of the
    pages she needs (a recent-activity page, a post, its analytics) carries
    one.
    """
    if not isinstance(url, str) or not url.startswith(LINKEDIN_PREFIX):
        return False
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F or c == "\\" for c in url):
        return False
    rest = url[len(LINKEDIN_PREFIX) :]
    for _ in range(_DECODE_ROUNDS):
        rest = unquote(rest)
    lowered = rest.lower()
    # A leading slash makes ``//host`` with the prefix's own slash.
    if lowered.startswith(("/", *LINKEDIN_REDIRECTORS)):
        return False
    return "//" not in lowered and "\\" not in lowered


def _arguments(event: Any) -> dict[str, Any] | None:
    """The call's arguments, or ``None`` when they cannot be read.

    The engine builds ``data`` as ``{"name", "arguments"}``; the inner stack's
    shape is ``{"tool", "args"}``. Both are read. Anything that is not a
    mapping is unreadable, and unreadable is a denial for a browser call.
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


def _browser_call(tool: str, event: Any) -> dict[str, str]:
    """Decide one browser call. Fail-closed: every unmatched path denies."""
    if tool not in BROWSER_TOOLS:
        return {
            "result": "DENY",
            "reason": (
                f"Eva's browser is read-only; {tool} is not one of {sorted(BROWSER_TOOLS)}"
            ),
        }
    args = _arguments(event)
    if args is None:
        return {"result": "DENY", "reason": f"Could not read the arguments of {tool}"}
    extra = set(args) - BROWSER_TOOLS[tool]
    if extra:
        return {
            "result": "DENY",
            "reason": f"{tool} may not carry {sorted(extra)} for Eva",
        }
    if tool == "browser_navigate" and not linkedin_url_ok(args.get("url")):
        return {
            "result": "DENY",
            "reason": f"Eva's browser opens only {LINKEDIN_PREFIX} pages",
        }
    if tool == "browser_wait_for" and "time" in args:
        wait = args["time"]
        if (
            isinstance(wait, bool)
            or not isinstance(wait, (int, float))
            or not 0 < wait <= MAX_WAIT_SECONDS
        ):
            return {
                "result": "DENY",
                "reason": f"A wait is between 0 and {MAX_WAIT_SECONDS} seconds",
            }
    return {"result": "ALLOW", "reason": "Read-only linkedin.com browser call"}


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
    if name.startswith(f"{BROWSER_SERVER}__"):
        return _browser_call(name[len(BROWSER_SERVER) + 2 :], event)
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
        "reason": "Eva permits only the outreach and read-only browser tools in her spec",
    }

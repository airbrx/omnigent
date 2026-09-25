"""Structural guards for the four invariants of Iris's v2 account surface.

GET /v1/iris/account is the one Iris route that runs no model: it lists the
caller's bindings, reads the newest overview capture per tenant out of the
caller's own sessions, and hands summary rows to the pinned ``iris.account.rank``.
The behavioural tests (``test_iris_account_route.py``,
``test_iris_account_collect.py``) cover what the surface does today. These cover
the change somebody makes next. Each one parses the source rather than running
it — Python with ``ast``, the pinned archive straight out of the zip, the TSX as
text — and fails when a forbidden name, key, endpoint or clock reaches the
surface, the way ``tests/server/test_launch_env_every_path.py`` fails when a
launch frame skips its helper.

1. No model on the account surface. The handler and ``collect_captures`` never
   reach ``turn``, ``completed_answer`` or a ``/events`` stream, and the native
   client is used for ``get`` alone.
2. Summary rows only. Every row is built from a closed key set, listed
   literally below; only ``tenant_id`` and ``metrics`` are read out of a report.
3. Drill-in is unchanged. The account view makes no request of its own, and
   the drill-in it triggers is one ``POST /v1/sessions``.
4. Quarantine by coverage, never by age. ``incomplete_period`` reads
   ``period_complete``; no quarantine decision reads ``captured_at`` or ``now``.
"""

from __future__ import annotations

import ast
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IRIS = ROOT / "omnigent" / "airbrx" / "iris"
ROUTES = IRIS / "routes.py"
COLLECT = IRIS / "account.py"
ARCHIVE = IRIS / "iris-source.zip"
VIEW = ROOT / "web" / "src" / "shell" / "IrisAccountView.tsx"
WORKSPACE = ROOT / "web" / "src" / "shell" / "IrisWorkspace.tsx"

# --------------------------------------------------------------------------
# The closed key sets. Enumerated from what main emits; widening any of them is
# a product decision, not a test fix.
# --------------------------------------------------------------------------

#: A capture row from `collect_captures`. `metrics` is the report's metrics
#: block, handed whole to `rank`, which reads named figures out of it.
CAPTURE_ROW_KEYS = frozenset({"tenant_id", "metrics", "captured_at"})
#: The fail-closed row from `collect_captures` when a tenant could not be read.
ERROR_ROW_KEYS = frozenset({"error"})
#: The only fields `collect_captures` reads out of a report body.
REPORT_FIELDS_READ = frozenset({"tenant_id", "metrics"})
#: What the route adds around `rank`'s output.
ROUTE_TOP_LEVEL_KEYS = frozenset({"generated_at", "tenants"})
#: The tenant rows the route hands to `rank`.
TENANT_INPUT_KEYS = frozenset({"tenant_id", "name", "note"})
#: A ranked row from the pinned `iris.account.rank`.
RANKED_ROW_KEYS = frozenset(
    {
        "tenant_id",
        "name",
        "hit_rate",
        "hit_rate_denominator",
        "requests",
        "cache_misses",
        "covered_days",
        "requested_days",
        "captured_at",
        "age_seconds",
    }
)
#: Every quarantined row carries these.
QUARANTINED_ROW_KEYS = frozenset({"tenant_id", "name", "reason", "detail"})
#: A quarantined row may additionally carry these, and nothing else.
QUARANTINE_EXTRA_KEYS = frozenset({"covered_days", "requested_days", "captured_at"})

#: The three native reads the account surface is allowed to make. A `{}` stands
#: for an interpolated id.
COLLECT_READS = frozenset(
    {
        "/v1/sessions",
        "/v1/sessions/{}/items",
        "/v1/sessions/{}/resources/files/{}/content",
    }
)

#: Names that mean a model ran, or would run.
MODEL_NAMES = frozenset({"turn", "completed_answer"})
#: Client methods that write to a session. The account surface only reads.
WRITE_METHODS = frozenset({"post", "put", "patch", "delete", "stream", "send"})
#: Names that would make quarantine a function of time.
CLOCK_NAMES = frozenset({"now", "age", "age_seconds", "captured_at", "time", "datetime"})


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _archived_rank_module() -> ast.Module:
    """`iris/account.py` exactly as the pinned archive ships it.

    Read from the zip rather than through `source_root()`: what ships is the
    invariant, and reading it needs no extraction, no `sys.path` and no
    integrity check to have already passed.
    """
    with zipfile.ZipFile(ARCHIVE) as bundle:
        source = bundle.read("iris/account.py").decode()
    return ast.parse(source, filename=f"{ARCHIVE.name}:iris/account.py")


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(found) == 1, f"expected exactly one `def {name}`, found {len(found)}"
    return found[0]


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _attributes(node: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _strings(node: ast.AST) -> set[str]:
    return {
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _path_literal(node: ast.AST) -> str | None:
    """A string or f-string argument as a path template, `{}` per interpolation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append("{}")
        return "".join(parts)
    return None


def _literal_keys(node: ast.Dict) -> frozenset[str]:
    """The string keys of a dict literal. A `**spread` fails: its keys are open."""
    keys = []
    for key in node.keys:
        assert key is not None, f"line {node.lineno}: a `**` spread makes the row's keys open"
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
            f"line {node.lineno}: a computed key makes the row's keys open"
        )
        keys.append(key.value)
    return frozenset(keys)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _calls_to(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _tsx_interface_fields(source: str, name: str) -> tuple[set[str], set[str]]:
    """(required, optional) field names of an exported TSX interface."""
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert match, f"interface {name} not found"
    required, optional = set(), set()
    for field, optional_mark in re.findall(r"^\s*(\w+)(\??):", match.group(1), re.M):
        (optional if optional_mark else required).add(field)
    return required, optional


def _account_handler() -> ast.AsyncFunctionDef:
    handler = _function(_module(ROUTES), "account")
    decorators = [ast.unparse(d) for d in handler.decorator_list]
    assert "router.get('/iris/account')" in decorators, decorators
    return handler


# --------------------------------------------------------------------------
# 1. No model on the account surface
# --------------------------------------------------------------------------


def test_account_handler_never_reaches_the_model() -> None:
    handler = _account_handler()
    reached = _names(handler) & MODEL_NAMES
    assert not reached, f"GET /v1/iris/account reaches {sorted(reached)}: a model would run"
    writes = _attributes(handler) & WRITE_METHODS
    assert not writes, f"GET /v1/iris/account writes to a session via {sorted(writes)}"
    events = sorted(s for s in _strings(handler) if "/events" in s)
    assert not events, f"GET /v1/iris/account names an events stream: {events}"


def test_account_handler_uses_the_native_client_for_get_alone() -> None:
    """`session_client` is the caller-authenticated native API, not a model.

    The handler opens it to hand `client.get` to `collect_captures` and for
    nothing else: it never calls the client itself and never touches another
    method on it. That is what keeps the surface read-only end to end.
    """
    handler = _account_handler()
    clients: set[str] = set()
    for node in ast.walk(handler):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                expr = item.context_expr
                if (
                    isinstance(expr, ast.Call)
                    and isinstance(expr.func, ast.Name)
                    and expr.func.id == "session_client"
                ):
                    assert isinstance(item.optional_vars, ast.Name)
                    clients.add(item.optional_vars.id)
    assert clients, "the handler no longer opens session_client; re-check where reads come from"
    touched = {
        node.attr
        for node in ast.walk(handler)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in clients
    }
    assert touched == {"get"}, f"the handler touches client.{sorted(touched - {'get'})}"
    direct = [
        node.lineno
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in clients
    ]
    assert not direct, f"the handler calls the client itself at lines {direct}"
    passed = [
        call
        for call in _calls_to(handler, "collect_captures")
        if any(
            isinstance(arg, ast.Attribute)
            and arg.attr == "get"
            and isinstance(arg.value, ast.Name)
            and arg.value.id in clients
            for arg in call.args
        )
    ]
    assert passed, "client.get is not what the handler hands to collect_captures"


def test_collect_captures_only_reads_the_session_record() -> None:
    tree = _module(COLLECT)
    reached = _names(tree) & (MODEL_NAMES | {"session_client"})
    assert not reached, f"iris/account.py reaches {sorted(reached)}"
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = {
        m
        for m in imported
        if m.startswith(("omnigent.airbrx.iris.routes", "omnigent.airbrx.iris.runtime", "httpx"))
    }
    assert not forbidden, f"iris/account.py imports {sorted(forbidden)}"
    events = sorted(s for s in _strings(tree) if "/events" in s)
    assert not events, f"iris/account.py names an events stream: {events}"
    writes = [
        f"{node.lineno}: .{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in WRITE_METHODS
    ]
    assert not writes, f"iris/account.py writes to a session: {writes}"
    reads = set()
    for call in _calls_to(tree, "get"):
        assert call.args, f"line {call.lineno}: get() without a path"
        path = _path_literal(call.args[0])
        assert path is not None, f"line {call.lineno}: get() with a computed path"
        reads.add(path)
    assert reads == COLLECT_READS, (
        f"collect_captures reads {sorted(reads - COLLECT_READS)} beyond the session record"
        if reads - COLLECT_READS
        else f"collect_captures no longer reads {sorted(COLLECT_READS - reads)}"
    )


# --------------------------------------------------------------------------
# 2. Summary rows only
# --------------------------------------------------------------------------


def test_collect_captures_rows_carry_only_summary_keys() -> None:
    fn = _function(_module(COLLECT), "collect_captures")
    rows = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "result"
        ):
            continue
        if isinstance(node.value, ast.Constant) and node.value.value is None:
            continue
        assert isinstance(node.value, ast.Dict), f"line {node.lineno}: a row that is not a literal"
        rows.append((node.lineno, _literal_keys(node.value)))
    shapes = {keys for _, keys in rows}
    assert CAPTURE_ROW_KEYS in shapes, "no capture row found; the guard is not looking"
    assert ERROR_ROW_KEYS in shapes, "no error row found; the guard is not looking"
    stray = [
        (line, sorted(keys))
        for line, keys in rows
        if keys not in {CAPTURE_ROW_KEYS, ERROR_ROW_KEYS}
    ]
    assert not stray, f"rows with keys outside the closed set: {stray}"


def test_collect_captures_reads_only_tenant_id_and_metrics_from_a_report() -> None:
    """The report body stays in the store: `report` is touched only by
    `report.get("tenant_id")`, `report.get("metrics")` and the type check."""
    fn = _function(_module(COLLECT), "collect_captures")
    parents = _parents(fn)
    fields, misuse = set(), []
    for node in ast.walk(fn):
        if not (
            isinstance(node, ast.Name) and node.id == "report" and isinstance(node.ctx, ast.Load)
        ):
            continue
        parent = parents[node]
        if isinstance(parent, ast.Attribute) and parent.attr == "get":
            call = parents[parent]
            key = call.args[0] if isinstance(call, ast.Call) and call.args else None
            assert isinstance(key, ast.Constant), (
                f"line {node.lineno}: report.get with a computed key"
            )
            fields.add(key.value)
        elif (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "isinstance"
        ):
            continue
        else:
            misuse.append(f"line {node.lineno}: {type(parent).__name__}")
    assert not misuse, f"the report body escapes collect_captures at {misuse}"
    assert fields == REPORT_FIELDS_READ, f"collect_captures reads {sorted(fields)} out of a report"


def test_route_emits_only_the_summary_shape() -> None:
    handler = _account_handler()
    returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return) and n.value is not None]
    assert len(returns) == 1
    body = returns[0].value
    assert isinstance(body, ast.Dict)
    literal, spread = set(), []
    for key, value in zip(body.keys, body.values, strict=True):
        if key is None:
            spread.append(value)
        else:
            assert isinstance(key, ast.Constant)
            literal.add(key.value)
    assert literal == ROUTE_TOP_LEVEL_KEYS, (
        f"the route adds {sorted(literal - ROUTE_TOP_LEVEL_KEYS)}"
    )
    assert len(spread) == 1 and isinstance(spread[0], ast.Call)
    assert isinstance(spread[0].func, ast.Name) and spread[0].func.id == "rank", ast.unparse(
        spread[0]
    )
    tenant_rows = [
        n.elt
        for n in ast.walk(handler)
        if isinstance(n, ast.ListComp) and isinstance(n.elt, ast.Dict)
    ]
    assert len(tenant_rows) == 1, "expected one tenant-row comprehension"
    assert _literal_keys(tenant_rows[0]) == TENANT_INPUT_KEYS


def test_pinned_rank_emits_only_the_summary_shape() -> None:
    tree = _archived_rank_module()
    classify = _function(tree, "_classify")
    ranked = [
        n.value.elts[1]
        for n in ast.walk(classify)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Tuple)
        and isinstance(n.value.elts[0], ast.Constant)
        and n.value.elts[0].value == "ranked"
    ]
    assert len(ranked) == 1, "expected exactly one ranked-row return in _classify"
    assert isinstance(ranked[0], ast.Dict)
    assert _literal_keys(ranked[0]) == RANKED_ROW_KEYS, sorted(_literal_keys(ranked[0]))

    quarantine = _function(tree, "_quarantine")
    returns = [n.value for n in ast.walk(quarantine) if isinstance(n, ast.Return)]
    assert len(returns) == 1 and isinstance(returns[0], ast.Dict)
    base = frozenset(k.value for k in returns[0].keys if isinstance(k, ast.Constant))
    assert base == QUARANTINED_ROW_KEYS, sorted(base)
    extras = set()
    for call in _calls_to(classify, "_quarantine"):
        for kw in call.keywords:
            assert kw.arg is not None, (
                f"line {call.lineno}: a `**` spread makes the row's keys open"
            )
            extras.add(kw.arg)
    assert extras <= QUARANTINE_EXTRA_KEYS, (
        f"quarantine rows carry {sorted(extras - QUARANTINE_EXTRA_KEYS)}"
    )

    forbidden = next(
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "FORBIDDEN_ROW_KEYS"
    )
    listed = _strings(forbidden)
    emitted = RANKED_ROW_KEYS | QUARANTINED_ROW_KEYS | QUARANTINE_EXTRA_KEYS
    assert not (listed & emitted), f"a row key is also forbidden: {sorted(listed & emitted)}"


def test_ui_contract_matches_the_rows_the_server_emits() -> None:
    """The view builds a row from these named fields and nothing else, so the
    TSX interfaces are the same closed set, not a superset."""
    source = VIEW.read_text()
    required, optional = _tsx_interface_fields(source, "IrisRankedRow")
    assert required == RANKED_ROW_KEYS and not optional, (sorted(required), sorted(optional))
    required, optional = _tsx_interface_fields(source, "IrisQuarantinedRow")
    assert required == QUARANTINED_ROW_KEYS, sorted(required)
    assert optional == QUARANTINE_EXTRA_KEYS, sorted(optional)


# --------------------------------------------------------------------------
# 3. Drill-in is unchanged
# --------------------------------------------------------------------------


def test_account_view_makes_no_request_of_its_own() -> None:
    source = VIEW.read_text()
    lowered = source.lower()
    for marker in (
        "fetch(",
        "usequery",
        "usemutation",
        "xmlhttprequest",
        "eventsource",
        "websocket",
    ):
        assert marker not in lowered, f"IrisAccountView.tsx makes a request via {marker!r}"
    assert "onOpen(entry.tenant_id)" in source, "the drill-in is no longer the onOpen callback"


def test_drill_in_calls_only_post_v1_sessions() -> None:
    source = WORKSPACE.read_text()
    assert "onOpen={(tenantId) => void create(tenantId)}" in source, (
        "the account view's onOpen is no longer wired straight to create()"
    )
    start = source.index("async function create(")
    end = source.index("\n  }\n", start)
    create = source[start:end]
    calls = re.findall(r"(?:authenticatedFetch|fetch)\(\s*[\"'`]([^\"'`]+)[\"'`]", create)
    assert calls == ["/v1/sessions"], f"the drill-in calls {calls}"
    assert 'method: "POST"' in create, "the drill-in no longer POSTs"
    assert "/events" not in create and "/ui/api" not in create, "the drill-in reaches a turn"


# --------------------------------------------------------------------------
# 4. Quarantine by coverage, never by age
# --------------------------------------------------------------------------


def _incomplete_period_decision(classify: ast.AST) -> ast.If:
    decisions = [
        node
        for node in ast.walk(classify)
        if isinstance(node, ast.If)
        and any(
            any(isinstance(a, ast.Constant) and a.value == "incomplete_period" for a in call.args)
            for stmt in node.body
            for call in ast.walk(stmt)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_quarantine"
        )
    ]
    assert len(decisions) == 1, f"expected one incomplete_period decision, found {len(decisions)}"
    return decisions[0]


def test_incomplete_period_is_decided_by_coverage_not_age() -> None:
    classify = _function(_archived_rank_module(), "_classify")
    decision = _incomplete_period_decision(classify)
    read = _names(decision.test) | _attributes(decision.test)
    assert read == {"metrics", "get"}, f"incomplete_period reads {sorted(read)}"
    assert _strings(decision.test) == {"period_complete"}, sorted(_strings(decision.test))
    assert not (read & CLOCK_NAMES), (
        f"incomplete_period reads the clock: {sorted(read & CLOCK_NAMES)}"
    )


def test_no_quarantine_decision_reads_the_clock() -> None:
    """`now` exists to stamp `age_seconds` on a ranked row and for nothing else.

    `captured_at` may be *type-checked* (a capture without a timestamp is
    `unreadable_config`), but no decision compares it to anything.
    """
    tree = _archived_rank_module()
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    clocks = {m for m in imported if m.split(".")[0] in {"time", "datetime"}}
    assert not clocks, f"iris.account keeps a clock: {sorted(clocks)}"

    classify = _function(tree, "_classify")
    for node in ast.walk(classify):
        if not isinstance(node, ast.If):
            continue
        read = _names(node.test)
        clocked = sorted(read & {"now", "age_seconds"})
        assert not clocked, f"line {node.lineno}: a quarantine decision reads {clocked}"
        if "captured_at" in read:
            compares = [n for n in ast.walk(node.test) if isinstance(n, ast.Compare)]
            assert not compares, f"line {node.lineno}: captured_at is compared, not type-checked"
            calls = {
                n.func.id
                for n in ast.walk(node.test)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            assert calls == {"isinstance"}, (
                f"line {node.lineno}: captured_at feeds {sorted(calls)}"
            )
    for call in _calls_to(classify, "_quarantine"):
        assert "now" not in _names(call), f"line {call.lineno}: now reaches a quarantined row"
    ages = [
        n
        for n in ast.walk(classify)
        if isinstance(n, ast.Name) and n.id == "now" and isinstance(n.ctx, ast.Load)
    ]
    parents = _parents(classify)
    for age in ages:
        node = age
        while node in parents and not isinstance(node, ast.Dict):
            node = parents[node]
        assert isinstance(node, ast.Dict) and _literal_keys(node) == RANKED_ROW_KEYS, (
            f"line {age.lineno}: now is read outside the ranked row"
        )

"""
Importable entry point for the vendored Iris tool boundary.

The boundary itself lives in `airbrx/iris` (`iris/policy.py`) and is tested
there — `iris #22` pins that 13 forbidden tools still DENY, with `ToolSearch`
deliberately allowed so fixing the gate later cannot break Iris. None of that
logic belongs here; this module only makes it reachable.

Why it has to exist
-------------------
The agent spec named `iris.policy.tool_boundary` directly, and on the
execution host that import fails:

    ERROR runner.policy from_spec | runner policy failed to resolve
      (ModuleNotFoundError); function path 'iris.policy.tool_boundary'
      could not be loaded; tool calls are denied until this policy is fixed

`iris` is not a package in any environment that runs Omnigent. It ships as a
verified zip inside this directory, and it reaches `sys.path` in exactly one
place: `source_root()`. The coordinator calls that when it serves the
workspace, and `runtime.py` passes the extraction to the *Iris tool
subprocess* as `PYTHONPATH`. The **runner** process — which resolves spec
policies at startup, on a different machine — calls neither, so the name has
never been importable there.

Routing the spec through `omnigent.airbrx.iris.policy` fixes that without
moving the boundary out of the repository that owns it: this module is part
of Omnigent, so it is importable wherever a runner runs, including a
`uv tool install --auto-upgrade` host where anything installed alongside it
would be wiped on the next upgrade.

`source_root()` is `lru_cache`d and verifies the archive's sha256 before
extracting, so calling it here costs one extraction per process and re-uses
the same integrity check the rest of the package relies on.
"""

from __future__ import annotations

from typing import Any

from omnigent.airbrx.iris.package import source_root


def tool_boundary(event: Any) -> Any:
    """
    Evaluate one TOOL_CALL event against the vendored Iris boundary.

    Deliberately thin. It takes no view on what should be allowed — a second
    opinion living here could disagree with the one the iris repository tests,
    and a boundary that two files describe differently is worse than one that
    fails to load, because it fails quietly.

    Raises whatever `source_root()` raises when the archive is missing or its
    digest does not match. That is the correct outcome: the runner's resolver
    is fail-closed, so a raise here denies tool calls rather than opening them.
    """
    # Extract (once per process), verify the digest, and put the bundled
    # `iris` package on sys.path. Imported inside the function because the
    # module does not exist until this call has run.
    source_root()

    from iris.policy import tool_boundary as _vendored

    return _vendored(event)

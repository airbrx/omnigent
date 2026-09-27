"""Which kernel files an agent workspace may serve, and where each one lives.

An agent's asset route serves ``kernel/<name>`` by calling :func:`kernel_asset`.
Only the names in :data:`KERNEL_ASSETS` resolve; anything else, including a path
that tries to leave ``ui/``, answers ``None`` and the route returns 404.
It returns the path only: the route wraps the result in ``Path`` itself.
"""

from __future__ import annotations

from pathlib import Path

#: The kernel's directory, packaged as ``omnigent.airbrx.workspace`` ``ui/**/*``.
UI_ROOT = Path(__file__).resolve().parent / "ui"

#: Every file a workspace may fetch from the kernel, with its media type, in the
#: order a page loads the scripts (``dom.js`` first, since the others use it).
KERNEL_ASSETS: dict[str, str] = {
    "dom.js": "text/javascript",
    "theme.js": "text/javascript",
    "api.js": "text/javascript",
    "markdown.js": "text/javascript",
    "transcript.js": "text/javascript",
    "stream.js": "text/javascript",
    "dock.js": "text/javascript",
    "tabs.js": "text/javascript",
    "context.js": "text/javascript",
    "brand.css": "text/css",
}


def kernel_asset(name: str) -> Path | None:
    """The file for kernel asset ``name``, or ``None`` when it is not served.

    The route (``omnigent/airbrx/iris/routes.py`` ``kernel_file``) serves the
    path with ``Cache-Control: no-store``; its media type is the one listed in
    :data:`KERNEL_ASSETS`, which is also what the extension implies.
    """
    if name not in KERNEL_ASSETS:
        return None
    return UI_ROOT / name

"""Which kernel files an agent workspace may serve, and where each one lives.

An agent's asset route serves ``kernel/<name>`` by calling :func:`kernel_asset`.
Only the names in :data:`KERNEL_ASSETS` resolve; anything else, including a path
that tries to leave ``ui/``, answers ``None`` and the route returns 404.
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


def kernel_asset(name: str) -> tuple[Path, str] | None:
    """The file and media type for kernel asset ``name``, or ``None``.

    Serve the result with ``Cache-Control: no-store``, as the agent's own
    ``app.js`` and ``style.css`` are.
    """
    media_type = KERNEL_ASSETS.get(name)
    if media_type is None:
        return None
    return UI_ROOT / name, media_type

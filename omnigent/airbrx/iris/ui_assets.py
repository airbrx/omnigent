"""Which workspace files a host serves, and how: one rule set for every Iris host.

The Omnigent adapter (`routes.py`) and the standalone viewer both answer
`ui/{asset}` through `ui_response`, so the two cannot serve different files
(docs/iris/STANDALONE_VIEWER.md, section 7). Authentication stays with the
host; this module only decides what a name resolves to.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from starlette.responses import FileResponse

from omnigent.airbrx.iris.package import HERE, source_root

#: Where the v2 workspace (docs/iris/WORKSPACE_V2.md, D1) lives in this package.
UI_ROOT = HERE / "ui"
#: The v2 app files served from `UI_ROOT`. A pattern, not a directory listing,
#: so nothing else placed under `ui/` becomes reachable, and `views/` cannot be
#: escaped with `..` or a nested path.
APP_FILES = re.compile(r"(?:app\.js|style\.css|views/[A-Za-z0-9_-]+\.js)")
#: The two images still read from the pinned archive (`ui/assets/`, D1).
PINNED_IMAGES = frozenset({"assets/iris-portrait.png", "assets/airbrx-logo.png"})

NO_STORE = {"Cache-Control": "no-store"}
INDEX_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"}
IMAGE_HEADERS = {"Cache-Control": "private, max-age=3600"}


def kernel_file(name: str) -> Path | None:
    """The shared workspace kernel file `name`, or None when it is not served.

    The kernel (`omnigent.airbrx.workspace`) owns its allowlist; this only
    refuses anything outside it. Imported by name at call time, so a host
    without the kernel 404s kernel files instead of failing to import.
    """
    try:
        kernel = importlib.import_module("omnigent.airbrx.workspace.assets")
    except ImportError:
        return None
    if name not in getattr(kernel, "KERNEL_ASSETS", ()):
        return None
    try:
        path = kernel.kernel_asset(name)
    except (KeyError, ValueError, OSError):
        return None
    if path is None:
        return None
    path = Path(path)
    return path if path.is_file() else None


def ui_response(asset: str) -> FileResponse | None:
    """The response for workspace file `asset` (the path after `ui/`), or None for a 404.

    `index.html` is `no-store` and same-origin framed only; the app and kernel
    files are `no-store`; the two pinned images come from the verified archive.
    Everything else, including `host.js`, `iris-state.json` and
    `demo-state.json`, is None.
    """
    if not asset or asset == "index.html":
        index = UI_ROOT / "index.html"
        if not index.is_file():
            return None
        return FileResponse(index, media_type="text/html", headers=INDEX_HEADERS)
    if APP_FILES.fullmatch(asset):
        path = UI_ROOT / asset
        return FileResponse(path, headers=NO_STORE) if path.is_file() else None
    if asset.startswith("kernel/"):
        path = kernel_file(asset.removeprefix("kernel/"))
        return None if path is None else FileResponse(path, headers=NO_STORE)
    if asset in PINNED_IMAGES:
        return FileResponse(source_root() / "ui" / asset, headers=IMAGE_HEADERS)
    return None

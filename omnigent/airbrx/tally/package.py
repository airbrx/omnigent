"""Locate Tally's agent bundle and portrait.

The same shape as ``airbrx/eva/package.py``: Tally's tools are remote MCP calls
to the portal sidecar, so her bundle is two static files tracked in this
repository and read where they lie. No archive, so no digest to check.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


@functools.lru_cache(maxsize=1)
def bundle_root() -> Path:
    """The directory holding Tally's ``config.yaml`` and ``AGENTS.md``."""
    root = HERE / "bundle"
    config = root / "config.yaml"
    if not config.is_file():
        raise RuntimeError(f"Tally bundle is missing its config.yaml at {config}")
    if not (root / "AGENTS.md").is_file():
        raise RuntimeError("Tally bundle is missing AGENTS.md")
    return root


def is_tally(spec: Any) -> bool:
    """Is this spec Tally (or a fork of her)?

    ``name`` is read defensively for the reason ``is_eva`` records: this is
    handed duck-typed stand-ins that may carry no ``name`` at all, and an
    ``AttributeError`` here would take tool dispatch down for every agent.
    """
    name = getattr(spec, "name", None) or ""
    return bool(spec and (name == "tally" or name.startswith("tally (fork ")))


def portrait_path() -> Path:
    """Tally's avatar, a tracked asset.

    For now it is the Airbrx logomark, drawn by ``scripts/tally/make_portrait.py``.
    It is not a likeness; Abram will supply a real portrait.
    """
    return Path(__file__).resolve().parent / "assets" / "tally-portrait.png"

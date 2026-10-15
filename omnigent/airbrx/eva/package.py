"""Locate Eva's agent bundle.

Much smaller than ``airbrx/iris/package.py`` and the difference is the whole
point. Iris ships a 2.3MB zip of a separate Python package, verifies its
sha256, extracts it to a process-private temp directory, puts it on
``sys.path`` and imports it, because her tools are local Python that has to
exist somewhere at runtime. Eva's tools are remote MCP calls, so her bundle is
two static files that live in this repository and are read where they lie.

No archive means no integrity check to do here: the bundle is tracked in git and
is covered by whatever verified this checkout. Adding a vendored archive later
would mean adding the digest check back with it.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


@functools.lru_cache(maxsize=1)
def bundle_root() -> Path:
    """The directory holding Eva's ``config.yaml`` and ``AGENTS.md``."""
    root = HERE / "bundle"
    config = root / "config.yaml"
    if not config.is_file():
        raise RuntimeError(f"Eva bundle is missing its config.yaml at {config}")
    if not (root / "AGENTS.md").is_file():
        raise RuntimeError("Eva bundle is missing AGENTS.md")
    return root


def is_eva(spec: Any) -> bool:
    """Is this spec Eva (or a fork of her)?

    ``name`` is read defensively for the reason Iris's equivalent records: this
    gates tool dispatch and is handed whatever spec object the caller has,
    including duck-typed stand-ins carrying only the fields their own path
    needs. Reaching straight for ``spec.name`` turned an absent attribute into
    an ``AttributeError`` that took dispatch down for every agent, not just the
    one being checked.
    """
    name = getattr(spec, "name", None) or ""
    return bool(spec and (name == "eva" or name.startswith("eva (fork ")))


def portrait_path() -> Path:
    """Eva's avatar, a tracked asset rather than a member of an archive.

    Iris's portrait ships inside her vendored source zip because she vendors
    one. Eva does not, so hers lies in the tree next to the bundle it belongs
    with, and `scripts/eva/make_portrait.py` regenerates it.
    """
    return Path(__file__).resolve().parent / "assets" / "eva-portrait.png"

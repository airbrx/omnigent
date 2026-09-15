"""Load the unchanged, pinned Iris package; never a developer checkout."""

from __future__ import annotations

import functools
import hashlib
import importlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).parent


@functools.lru_cache(maxsize=1)
def source_root() -> Path:
    manifest = json.loads((HERE / "source.json").read_text())
    archive = HERE / "iris-source.zip"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest["sha256"]:
        raise RuntimeError("Iris package integrity check failed")
    # A process-private extraction avoids stale or mutable shared package caches.
    root = Path(tempfile.mkdtemp(prefix="omnigent-iris-")).resolve()
    os.chmod(root, 0o700)
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if not (root / name).resolve().is_relative_to(root):
                raise RuntimeError("Invalid Iris package member")
        bundle.extractall(root)
    sys.path.insert(0, str(root))
    iris = importlib.import_module("iris")

    if not iris.__file__ or not Path(iris.__file__).resolve().is_relative_to(root):
        raise RuntimeError("Another Iris package is already loaded; restart the host")
    return root


@functools.lru_cache(maxsize=1)
def bundle_root() -> Path:
    root = source_root() / "omnigent"
    config = root / "config.yaml"
    config.write_text(
        config.read_text()
        + (
            "\ndescription: Read-only cache analysis, evidence-linked findings, "
            "and proposals for review.\n"
        )
    )
    instructions = root / "AGENTS.md"
    text = instructions.read_text()
    for skill in sorted(root.glob("skills/*/SKILL.md")):
        text += "\n\n" + skill.read_text().split("---", 2)[-1].strip()
    instructions.write_text(text)
    return root


def is_iris(spec) -> bool:
    """Is this spec Iris (or a fork of her)?

    Read ``name`` defensively. This gates *every* tool dispatch
    (``runner/tool_dispatch.execute_tool``), so it is handed whatever spec
    object the caller has — including duck-typed stand-ins that carry only the
    fields their own code path needs. Reaching straight for ``spec.name`` made
    an absent attribute an ``AttributeError`` that took down the dispatch for
    every agent, Iris or not.
    """
    name = getattr(spec, "name", None) or ""
    return bool(spec and (name == "iris" or name.startswith("iris (fork ")))


def validate_spec(spec) -> None:
    from dataclasses import asdict

    from omnigent.spec.parser import parse

    expected = asdict(parse(bundle_root()))
    actual = asdict(spec)
    for key in ("name", "source_rel_dir"):
        actual.pop(key, None)
        expected.pop(key, None)
    for tool in actual["local_tools"]:
        expected_tool = next(
            (t for t in expected["local_tools"] if t["name"] == tool["name"]), None
        )
        if expected_tool and Path(tool["path"]).is_absolute():
            path = Path(tool["path"])
            if path.read_bytes() != (bundle_root() / expected_tool["path"]).read_bytes():
                raise ValueError("Iris tool source differs from the pinned bundle")
            tool["path"] = expected_tool["path"]
    if actual != expected:
        raise ValueError("Iris requires the pinned registered bundle without overrides")

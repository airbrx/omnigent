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
    # The same normalisation the tools above have had all along, for skills.
    #
    # `source_root()` extracts the verified archive into a per-process
    # `mkdtemp`, and `spec/parser.py` sets `skill_dir` to the absolute
    # directory it parsed the SKILL.md from (`spec/types.py` documents it as
    # absolute). So the registering process and the validating process produce
    # the same skills at different absolute paths, `actual != expected` below
    # is true for that reason alone, and EVERY Iris tool dispatch fails with
    # "Iris requires the pinned registered bundle without overrides" — which is
    # what shipped: zero Iris tools on a live production session.
    #
    # Unlike `local_tools[].path`, this does not need to read bytes to be safe.
    # A skill's identity is already in the comparison: `name`, `description`
    # and the full `content` of its SKILL.md are all fields of the same
    # dataclass and are all still compared. A genuinely different skill still
    # fails. `skill_dir` is the one field that cannot survive a temp directory,
    # and it is the only one being set aside.
    for skill in actual["skills"]:
        expected_skill = next((s for s in expected["skills"] if s["name"] == skill["name"]), None)
        if expected_skill and Path(skill["skill_dir"]).is_absolute():
            skill["skill_dir"] = expected_skill["skill_dir"]
    if actual != expected:
        raise ValueError("Iris requires the pinned registered bundle without overrides")

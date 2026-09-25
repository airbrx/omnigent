"""Structural guard: every runner launch frame the server builds carries the launch env.

The behavioural tests (``tests/server/integration/test_launch_env_launch_paths.py``)
cover the three launch paths that exist today. This covers the fourth one
somebody adds next: any ``HostLaunchRunnerFrame(...)`` built under
``omnigent/server`` must spread ``**launch_env_fields(...)`` into it, or an
agent whose credential is a host secret reference starts without it on that
path. That is exactly how #78 shipped: wired at one site of three.
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "omnigent" / "server"


def _launch_frames() -> list[tuple[Path, ast.Call]]:
    found = []
    for path in sorted(SERVER.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "HostLaunchRunnerFrame"
            ):
                found.append((path, node))
    return found


def _spreads_launch_env(call: ast.Call) -> bool:
    for kw in call.keywords:
        if (
            kw.arg is None
            and isinstance(kw.value, ast.Call)
            and isinstance(kw.value.func, ast.Name)
            and kw.value.func.id == "launch_env_fields"
        ):
            return True
    return False


def test_there_are_launch_sites_to_check() -> None:
    """A guard that finds nothing passes forever; make sure it is looking."""
    assert len(_launch_frames()) >= 3


def test_every_launch_frame_spreads_launch_env_fields() -> None:
    missing = [
        f"{path.relative_to(SERVER.parent.parent)}:{call.lineno}"
        for path, call in _launch_frames()
        if not _spreads_launch_env(call)
    ]
    assert not missing, (
        "these HostLaunchRunnerFrame(...) calls do not spread **launch_env_fields(...), so "
        "an agent that needs a host secret reference launches without it there: "
        + ", ".join(missing)
    )


def test_no_launch_frame_sets_the_fields_by_hand() -> None:
    """Setting agent_env / agent_secret_env directly would bypass the one helper."""
    by_hand = [
        f"{path.relative_to(SERVER.parent.parent)}:{call.lineno}"
        for path, call in _launch_frames()
        if any(kw.arg in {"agent_env", "agent_secret_env"} for kw in call.keywords)
    ]
    assert not by_hand, ", ".join(by_hand)

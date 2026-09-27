"""The airbrx workspace kernel's allowlist and source rules.

Behaviour is tested in ``web/src/shell/workspaceKernel.test.ts``, which runs the
scripts in jsdom. This file holds what is about the files themselves: which ones
an asset route may serve, and what the source must never contain.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from omnigent.airbrx.workspace.assets import KERNEL_ASSETS, UI_ROOT, kernel_asset


def test_the_allowlist_is_exactly_the_kernel_files() -> None:
    on_disk = {p.name for p in UI_ROOT.iterdir() if p.is_file()}
    assert set(KERNEL_ASSETS) == on_disk
    for name, media_type in KERNEL_ASSETS.items():
        path = kernel_asset(name)
        assert isinstance(path, Path)
        assert path.is_file() and path.parent == UI_ROOT
        assert media_type == ("text/css" if name.endswith(".css") else "text/javascript")


@pytest.mark.parametrize(
    "name",
    [
        "",
        "nope.js",
        "../assets.py",
        "../__init__.py",
        "ui/dom.js",
        "/etc/passwd",
        "dom.js/",
        "DOM.JS",
        "..%2Fassets.py",
    ],
)
def test_anything_else_is_not_an_asset(name: str) -> None:
    assert kernel_asset(name) is None


SOURCES = sorted(p for p in UI_ROOT.iterdir() if p.suffix in {".js", ".css"})


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_em_dash(path) -> None:
    assert "—" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [p for p in SOURCES if p.suffix == ".js"], ids=lambda p: p.name)
def test_data_is_never_rendered_as_markup(path) -> None:
    source = path.read_text(encoding="utf-8")
    sinks = (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "DOMParser",
        "createContextualFragment",
        "eval(",
    )
    for sink in sinks:
        assert sink not in source, sink


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_the_kernel_is_agent_agnostic(path) -> None:
    source = path.read_text(encoding="utf-8")
    for name in ("eva", "iris", "outreach", "lead"):
        assert not re.search(rf"\b{name}", source, re.IGNORECASE), name


# --- The real kernel through the real Iris route (OMNIGENT_IRIS_UI=v2) ------------------
#
# No stand-in kernel: this is the merged `omnigent/airbrx/iris/routes.py` asset
# route calling this package's `kernel_asset`, so a contract drift between the
# two (as when kernel_asset returned a tuple the route wraps in Path) fails here.


@pytest.mark.parametrize("name", sorted(KERNEL_ASSETS))
def test_the_iris_v2_route_serves_every_kernel_file(monkeypatch, tmp_path, name) -> None:
    from tests.airbrx.test_iris_workspace_fixtures import (
        FILES,
        SESSION,
        IrisSession,
        captured,
        make_client,
    )

    monkeypatch.setenv("OMNIGENT_IRIS_UI", "v2")
    client = make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    response = client.get(f"/v1/iris/sessions/{SESSION}/ui/kernel/{name}")
    assert response.status_code == 200, response.text
    assert response.content == (UI_ROOT / name).read_bytes()
    assert response.headers["cache-control"] == "no-store"
    expected = "text/css" if name.endswith(".css") else "javascript"
    assert expected in response.headers["content-type"]


@pytest.mark.parametrize("name", ["nope.js", "..%2Fassets.py", "", "DOM.JS"])
def test_the_iris_v2_route_refuses_anything_else(monkeypatch, tmp_path, name) -> None:
    from tests.airbrx.test_iris_workspace_fixtures import (
        FILES,
        SESSION,
        IrisSession,
        captured,
        make_client,
    )

    monkeypatch.setenv("OMNIGENT_IRIS_UI", "v2")
    client = make_client(monkeypatch, tmp_path, IrisSession(captured(), FILES))
    assert client.get(f"/v1/iris/sessions/{SESSION}/ui/kernel/{name}").status_code == 404

"""The pinned Iris archive carries `iris.account`, and the pin is on iris stage.

The route in `routes.py` imports `iris.account` through `source_root()`, the
same path the policy re-export uses. If the archive predates the module, the
route raises on first use in production and nowhere else — so the archive is
checked here, in the process that ships it.
"""

import json
import subprocess
import zipfile

import pytest

from omnigent.airbrx.iris.package import HERE, source_root


def test_the_pinned_archive_carries_iris_account():
    source_root()
    from iris.account import QUARANTINE_REASONS, rank

    assert callable(rank)
    assert QUARANTINE_REASONS[0] == "tenant_mismatch"


def test_the_manifest_lists_the_module():
    manifest = json.loads((HERE / "source.json").read_text())
    assert "iris/account.py" in manifest["files"]
    assert "tests/test_account.py" not in manifest["files"], "vendor.py ships package files only"


#: The pinned v1 app. v2 is the only workspace since the cutover (W5) and
#: `airbrx/iris` marks `ui/` dev-only, so the archive stops carrying it.
V1_UI = frozenset({"ui/index.html", "ui/app.js", "ui/style.css", "ui/theme.js"})
#: What the archive still ships from `ui/`: the two images the v2 route reads
#: out of it (`ui_assets.PINNED_IMAGES`) and their provenance note.
UI_ASSETS = frozenset(
    {"ui/assets/PROVENANCE.md", "ui/assets/airbrx-logo.png", "ui/assets/iris-portrait.png"}
)


def _archive_members() -> set[str]:
    with zipfile.ZipFile(HERE / "iris-source.zip") as archive:
        return set(archive.namelist())


def test_the_archive_no_longer_carries_the_v1_ui():
    members = _archive_members()
    assert not V1_UI & members, f"v1 UI still vendored: {sorted(V1_UI & members)}"
    assert not V1_UI & set(json.loads((HERE / "source.json").read_text())["files"])


def test_the_archive_ships_only_the_ui_assets_from_ui():
    members = _archive_members()
    assert {m for m in members if m.startswith("ui/")} == UI_ASSETS
    # Captured tenant evidence and the synthetic demo never travel.
    assert not {"ui/iris-state.json", "ui/demo-state.json"} & members


def test_the_manifest_lists_exactly_the_archive():
    assert sorted(_archive_members()) == json.loads((HERE / "source.json").read_text())["files"]


def test_the_pin_is_a_commit_on_iris_stage():
    # A branch head can be squashed out of existence after the archive is cut
    # (that is how fb0c4daa happened). A commit reachable from stage cannot.
    # airbrx/iris has no `main`; `stage` is its default branch. "On stage" is
    # the invariant; "is stage's head" is too strict, stage moves between
    # re-vendors.
    revision = json.loads((HERE / "source.json").read_text())["revision"]
    try:
        compare = subprocess.run(
            ["gh", "api", f"repos/airbrx/iris/compare/{revision}...stage", "--jq", ".status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        pytest.skip("gh or the iris remote is unavailable from this environment")
    if compare.returncode != 0:
        pytest.skip("gh or the iris remote is unavailable from this environment")
    status = compare.stdout.strip()
    # identical: pin is stage's head; ahead: stage is ahead of the pin, i.e. the
    # pin is an ancestor. behind/diverged: the pin is not on stage.
    assert status in {"identical", "ahead"}, (
        f"pinned {revision[:8]} is not on iris stage ({status})"
    )

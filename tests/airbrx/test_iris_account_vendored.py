"""The pinned Iris archive carries `iris.account`, and the pin is on iris main.

The route in `routes.py` imports `iris.account` through `source_root()`, the
same path the policy re-export uses. If the archive predates the module, the
route raises on first use in production and nowhere else — so the archive is
checked here, in the process that ships it.
"""

import json
import subprocess

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


def test_the_pin_is_a_commit_on_iris_main():
    # A branch head can be squashed out of existence after the archive is cut
    # (that is how fb0c4daa happened). A commit reachable from main cannot.
    # "On main" is the invariant; "is main's head" was too strict — iris main
    # legitimately moves between re-vendors.
    revision = json.loads((HERE / "source.json").read_text())["revision"]
    try:
        compare = subprocess.run(
            ["gh", "api", f"repos/airbrx/iris/compare/{revision}...main", "--jq", ".status"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        pytest.skip("gh or the iris remote is unavailable from this environment")
    if compare.returncode != 0:
        pytest.skip("gh or the iris remote is unavailable from this environment")
    status = compare.stdout.strip()
    # identical: pin is main's head; ahead: main is ahead of the pin, i.e. the
    # pin is an ancestor. behind/diverged: the pin is not on main.
    assert status in {"identical", "ahead"}, (
        f"pinned {revision[:8]} is not on iris main ({status})"
    )

"""The pinned Iris archive carries `iris.account`, and the pin is a merge commit.

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
    # (that is how fb0c4daa happened). A merge commit on main cannot.
    revision = json.loads((HERE / "source.json").read_text())["revision"]
    try:
        remote = subprocess.run(
            ["git", "ls-remote", "git@github.com:airbrx/iris.git", "refs/heads/main"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        pytest.skip("iris remote unreachable from this environment")
    if remote.returncode != 0:
        pytest.skip("iris remote unreachable from this environment")
    main_head = remote.stdout.split()[0]
    assert revision == main_head, f"pinned {revision[:8]} but iris main is {main_head[:8]}"

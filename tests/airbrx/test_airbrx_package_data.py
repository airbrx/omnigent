"""Every non-Python file the airbrx agents need has to reach the wheel.

Eva's bundle is `config.yaml` and `AGENTS.md` in a plain directory rather than
a package, so `tool.setuptools.packages.find` does not see it. Without a
`package-data` entry the wheel carried her Python modules and no bundle, which
makes `bundle_root()` raise and a host installed from `install.sh` unable to
register her at all.

It survived a production deploy because production runs from source. That is
the failure this module exists to catch: a check that only ever runs against
the working tree cannot see a packaging mistake, so this one reads the
declaration instead of the filesystem it was built from.
"""

from __future__ import annotations

import fnmatch
import pathlib
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AIRBRX = REPO_ROOT / "omnigent" / "airbrx"

# Files that are not data and are never meant to ship as data.
_IGNORED_SUFFIXES = {".py", ".pyc"}
_IGNORED_DIRS = {"__pycache__"}


def _declared_globs() -> dict[str, list[str]]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["setuptools"]["package-data"]


def _data_files() -> list[tuple[str, str]]:
    """Every shippable non-Python file under omnigent/airbrx, as (package, relpath)."""
    found: list[tuple[str, str]] = []
    for path in sorted(AIRBRX.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in _IGNORED_SUFFIXES:
            continue
        if any(part in _IGNORED_DIRS for part in path.relative_to(AIRBRX).parts):
            continue
        # The owning package is the nearest ancestor holding __init__.py.
        package_dir = path.parent
        while not (package_dir / "__init__.py").exists():
            package_dir = package_dir.parent
            if package_dir == REPO_ROOT:
                pytest.fail(f"{path} sits outside any package")
        dotted = ".".join(package_dir.relative_to(REPO_ROOT).parts)
        found.append((dotted, path.relative_to(package_dir).as_posix()))
    return found


def _matches(patterns: list[str], relpath: str) -> bool:
    # setuptools treats ** as spanning directories; fnmatch does not, so a
    # `bundle/**/*` pattern is also tried with the `**/` collapsed.
    for pattern in patterns:
        if fnmatch.fnmatch(relpath, pattern):
            return True
        if fnmatch.fnmatch(relpath, pattern.replace("**/", "")):
            return True
    return False


def test_every_airbrx_data_file_is_declared_package_data() -> None:
    """A file the agents read at runtime, absent from the wheel, is a broken install.

    The failure is invisible from a source checkout and invisible in CI that
    runs from a source checkout, so it surfaces on somebody else's machine as
    an agent that cannot start.
    """
    declared = _declared_globs()
    missing: list[str] = []

    for package, relpath in _data_files():
        patterns = declared.get(package, [])
        if not _matches(patterns, relpath):
            missing.append(f"{package}:{relpath}")

    assert not missing, (
        "these files are read at runtime and would not ship in a wheel; add a "
        "[tool.setuptools.package-data] entry for each: " + ", ".join(missing)
    )


def test_evas_bundle_is_the_case_this_was_written_for() -> None:
    """Named explicitly so the general test above cannot pass vacuously.

    If the bundle ever moves and nobody notices, the loop finds nothing to
    check and reports success. This asserts the two files are actually there.
    """
    bundle = AIRBRX / "eva" / "bundle"

    assert (bundle / "config.yaml").is_file()
    assert (bundle / "AGENTS.md").is_file()
    assert _matches(_declared_globs()["omnigent.airbrx.eva"], "bundle/config.yaml")

"""Saved Iris runs on disk: the layout, the writer and the verifier.

A run is what the Omnigent adapter would have answered for one tenant as of
one capture (docs/iris/STANDALONE_VIEWER.md, section 3). The exporter writes
runs; the standalone viewer reads them. Both go through this module, so the
layout, the path rules and the integrity check are defined once.

::

    <runs_root>/
      <tenant_id>/
        tenant.json                {tenant_id, name, fixture}
        <YYYY>-W<ww>/              ISO week of the capture's UTC day
          manifest.json            provenance + sha256 of every other file in the run
          state.json               the adapter state body (WORKSPACE_V2 section 2)
          items.json               {data: [message items], has_more: false}
          files/index.json         {data: [{id, filename, bytes}]}
          files/<id>               report.json | report.md | proposal.json bytes

Stdlib only, and no import of the server or the adapter routes: the viewer
imports this module (section 1).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Section 3.1. A fixture tenant, or an Airbrx tenant id (a lowercase UUID).
TENANT_RE = re.compile(r"^(?:fixture-[a-z0-9-]{1,56}|[a-z0-9][a-z0-9-]{0,62})$")
#: The ISO week of a capture's UTC day.
WEEK_RE = re.compile(r"^[0-9]{4}-W[0-9]{2}$")
#: A stored file id. Omnigent's are `file_<hex>` or plain hex; nothing with a
#: dot or a slash ever reaches the filesystem.
FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

#: The only files the app lists under Downloads (`DOWNLOADS` in ui/app.js).
DOWNLOADS = ("report.json", "report.md", "proposal.json")

#: Section 3.3: how old a capture may be before the workspace calls it stale.
#: The same number as `workspace.STALE_AFTER_SECONDS`, spelled out so this
#: module stays stdlib-only; a test pins that the two agree.
STALE_AFTER_SECONDS = 300

SCHEMA = 1
SOURCE_KINDS = ("omnigent-session", "synthetic")

MANIFEST = "manifest.json"
STATE = "state.json"
ITEMS = "items.json"
INDEX = "files/index.json"
TENANT = "tenant.json"


class RunUnreadable(Exception):
    """A saved run fails its checks; it is listed as unreadable and never served (rule V3)."""


class RefusedOutput(Exception):
    """The output path is one real tenant evidence must never be written to (rule V4)."""


def valid_tenant(tenant_id: object) -> bool:
    return isinstance(tenant_id, str) and bool(TENANT_RE.fullmatch(tenant_id))


def valid_week(week: object) -> bool:
    return isinstance(week, str) and bool(WEEK_RE.fullmatch(week))


def valid_file_id(file_id: object) -> bool:
    return isinstance(file_id, str) and bool(FILE_ID_RE.fullmatch(file_id))


def iso_week(epoch_seconds: float) -> str:
    """`2026-W38` for a capture's UTC day. Never the report's own period (QA N2)."""
    year, week, _ = datetime.fromtimestamp(epoch_seconds, timezone.utc).isocalendar()
    return f"{year:04d}-W{week:02d}"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_json(value: Any) -> bytes:
    """The one serialisation every run file uses, so a rewrite of the same run is byte-equal."""
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def served_state(state: dict, now: float) -> dict:
    """The state as served at `now`: section 3.3's two clock fields, nothing else.

    `cache_age_seconds` and `stale` are recomputed from `captured_at` exactly
    as the adapter does. Every other key is returned as saved (rule V2).
    """
    age = max(0, now - state["captured_at"])
    return {**state, "cache_age_seconds": round(age), "stale": age > STALE_AFTER_SECONDS}


def git_worktree(path: Path) -> Path | None:
    """The git worktree `path` would land in, or None.

    Walks up from the nearest existing ancestor of the resolved path, so a
    directory that does not exist yet is judged by where it would be created,
    and a symlink is judged by where it points.
    """
    probe = Path(os.path.expanduser(path)).resolve()
    for candidate in (probe, *probe.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


# --- One run, in memory -------------------------------------------------------------


@dataclass
class SavedFile:
    id: str
    filename: str
    content: bytes


@dataclass
class Run:
    """One tenant's newest current-period capture in one ISO week, ready to write."""

    tenant_id: str
    state: dict
    items: list[dict]
    files: list[SavedFile]
    #: `manifest.source` (section 3.2): kind, server, session_id,
    #: through_item_id, omnigent_version, iris_revision.
    source: dict

    @property
    def captured_at(self) -> float:
        return float(self.state["captured_at"])

    @property
    def week(self) -> str:
        return iso_week(self.captured_at)

    @property
    def period(self) -> dict:
        metrics = self.state["overview"].get("metrics") or {}
        return {
            key: metrics.get(key)
            for key in (
                "start_date",
                "end_date",
                "covered_days",
                "requested_days",
                "period_complete",
            )
        }

    def contents(self) -> dict[str, bytes]:
        """Every file of the run but the manifest, by path relative to the run directory."""
        out = {
            STATE: dump_json(self.state),
            ITEMS: dump_json({"data": self.items, "has_more": False}),
            INDEX: dump_json(
                {
                    "data": [
                        {"id": f.id, "filename": f.filename, "bytes": len(f.content)}
                        for f in self.files
                    ]
                }
            ),
        }
        for f in self.files:
            out[f"files/{f.id}"] = f.content
        return out

    def manifest(self, contents: dict[str, bytes], exported_at: float) -> dict:
        return {
            "schema": SCHEMA,
            "tenant_id": self.tenant_id,
            "week": self.week,
            "captured_at": self.captured_at,
            "period": self.period,
            "source": self.source,
            "exported_at": exported_at,
            "files": {path: sha256(data) for path, data in sorted(contents.items())},
        }


def check_run(run: Run) -> None:
    """Refuse a run the layout cannot hold, before anything touches the disk."""
    if not valid_tenant(run.tenant_id):
        raise ValueError(f"not a tenant id this layout accepts: {run.tenant_id!r}")
    if run.state["overview"].get("tenant_id") != run.tenant_id:
        raise ValueError("the run's overview names another tenant")
    if run.source.get("kind") not in SOURCE_KINDS:
        raise ValueError(f"unknown run source kind: {run.source.get('kind')!r}")
    seen = set()
    for f in run.files:
        if not valid_file_id(f.id) or f.filename not in DOWNLOADS or f.id in seen:
            raise ValueError(f"not a file this layout accepts: {f.id!r} {f.filename!r}")
        seen.add(f.id)
    for item in run.items:
        if item.get("type") != "message" or item.get("role") not in ("user", "assistant"):
            raise ValueError("items.json holds only user and assistant messages (rule 15)")


# --- Writing --------------------------------------------------------------------------


def write_tenant(root: Path, tenant_id: str, *, name: str, fixture: bool) -> None:
    tenant_dir = Path(root) / tenant_id
    tenant_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    body = dump_json({"tenant_id": tenant_id, "name": name, "fixture": fixture})
    path = tenant_dir / TENANT
    if path.exists() and path.read_bytes() == body:
        return
    handle, temp = tempfile.mkstemp(dir=tenant_dir, prefix=".tenant-")
    with os.fdopen(handle, "wb") as out:
        out.write(body)
    os.replace(temp, path)


def write_run(
    root: Path,
    run: Run,
    *,
    name: str,
    fixture: bool,
    exported_at: float,
    force: bool = False,
) -> str:
    """Write one run. Returns "wrote", "replaced" or "kept".

    An existing run is replaced only when this capture is newer, or with
    `force`. Nothing of a real run is written inside a git worktree, checked
    for the root, the tenant directory and the run directory, so real tenant
    evidence never lands in a repository (rule V4). Only a synthetic fixture
    run (`fixture-` tenant) is exempt: the committed fixture. A symlinked
    tenant or run directory is refused for every run.

    A new run is staged and renamed into place in one step. Replacing a run
    takes two renames (the old run aside, then the new one in), so a reader
    in that instant sees no run, and a crash between them leaves the old run
    under `.<week>-old-*/run`. Acceptable for a laptop exporter.
    """
    check_run(run)
    root = Path(os.path.expanduser(root))
    exempt = (
        run.source.get("kind") == "synthetic" and fixture and run.tenant_id.startswith("fixture-")
    )
    tenant_dir = root / run.tenant_id
    target = tenant_dir / run.week
    for path in (tenant_dir, target):
        if path.is_symlink():
            raise RefusedOutput(f"{path} is a symlink; runs are written to plain directories")
        if path.exists() and not path.is_dir():
            raise RefusedOutput(f"{path} exists and is not a directory")
    if not exempt:
        for path in (root, tenant_dir, target):
            worktree = git_worktree(path)
            if worktree is not None:
                raise RefusedOutput(
                    f"{path} is inside the git worktree {worktree}; real tenant runs are "
                    "never written into a repository"
                )
    action = "wrote"
    if target.exists():
        try:
            existing = load_run(root, run.tenant_id, run.week).manifest
            newer = run.captured_at > float(existing["captured_at"])
        except (RunUnreadable, KeyError, TypeError, ValueError):
            newer = True
        if not newer and not force:
            return "kept"
        action = "replaced"
    contents = run.contents()
    manifest = run.manifest(contents, exported_at)
    new_tenant = not tenant_dir.exists()
    staging = None
    try:
        tenant_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=tenant_dir, prefix=f".{run.week}-"))
        (staging / "files").mkdir()
        for path, data in {**contents, MANIFEST: dump_json(manifest)}.items():
            (staging / path).write_bytes(data)
        if target.exists():
            retired = Path(tempfile.mkdtemp(dir=tenant_dir, prefix=f".{run.week}-old-"))
            os.replace(target, retired / "run")
            os.replace(staging, target)
            shutil.rmtree(retired, ignore_errors=True)
        else:
            os.replace(staging, target)
        # Last, so a failed first write leaves no tenant that has no runs.
        write_tenant(root, run.tenant_id, name=name, fixture=fixture)
    except BaseException:
        if new_tenant:
            shutil.rmtree(tenant_dir, ignore_errors=True)
        raise
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return action


# --- Reading and verifying ---------------------------------------------------------


@dataclass
class SavedRun:
    """A run directory as found on disk. `readable` is False when any check failed."""

    root: Path
    tenant_id: str
    week: str
    manifest: dict = field(default_factory=dict)
    problem: str | None = None

    @property
    def path(self) -> Path:
        return self.root / self.tenant_id / self.week

    @property
    def readable(self) -> bool:
        return self.problem is None

    def read(self, relpath: str) -> bytes:
        """One file's bytes, re-checked against the manifest on every read (rule V3)."""
        if self.problem is not None:
            raise RunUnreadable(self.problem)
        expected = self.manifest["files"].get(relpath)
        if expected is None:
            raise RunUnreadable(f"{relpath} is not part of this run")
        path = self.path / relpath
        if path.is_symlink() or not path.is_file():
            raise RunUnreadable(f"{relpath} is missing or not a plain file")
        data = path.read_bytes()
        if sha256(data) != expected:
            raise RunUnreadable(f"{relpath} does not match its checksum")
        return data

    def read_json(self, relpath: str) -> Any:
        return json.loads(self.read(relpath))

    def state(self) -> dict:
        return self.read_json(STATE)

    def items(self) -> dict:
        return self.read_json(ITEMS)

    def file_index(self) -> dict:
        return self.read_json(INDEX)

    def file(self, file_id: str) -> bytes:
        if not valid_file_id(file_id):
            raise RunUnreadable("not a file id")
        return self.read(f"files/{file_id}")

    def week_entry(self) -> dict:
        """One `data.weeks` row of the host descriptor (section 2.1)."""
        period = self.manifest.get("period") or {}
        return {
            "week": self.week,
            "captured_at": self.manifest.get("captured_at"),
            "start_date": period.get("start_date"),
            "end_date": period.get("end_date"),
            "period_complete": period.get("period_complete"),
            "covered_days": period.get("covered_days"),
            "requested_days": period.get("requested_days"),
            "readable": self.readable,
        }


#: Files the OS drops into folders a person opens (Finder on macOS). Not run
#: content: ignored when checking a run, and never served.
IGNORED_NAMES = frozenset({".DS_Store"})


def _run_files(run_dir: Path) -> set[str]:
    found = set()
    for path in run_dir.rglob("*"):
        if path.name in IGNORED_NAMES and path.is_file() and not path.is_symlink():
            continue
        if path.is_symlink():
            raise RunUnreadable(f"{path.relative_to(run_dir)} is a symlink")
        if path.is_file():
            found.add(path.relative_to(run_dir).as_posix())
    return found


def _verify(saved: SavedRun) -> None:
    run_dir = saved.path
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise RunUnreadable("the run is not a plain directory")
    try:
        manifest = json.loads((run_dir / MANIFEST).read_bytes())
    except (OSError, ValueError) as exc:
        raise RunUnreadable(f"the manifest cannot be read: {exc.__class__.__name__}") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise RunUnreadable("the manifest is not schema 1")
    saved.manifest = manifest
    if manifest.get("tenant_id") != saved.tenant_id or manifest.get("week") != saved.week:
        raise RunUnreadable("the manifest names another tenant or week than its directory")
    files = manifest.get("files")
    if not isinstance(files, dict) or not {STATE, ITEMS, INDEX} <= set(files):
        raise RunUnreadable("the manifest does not list the run's files")
    present = _run_files(run_dir) - {MANIFEST}
    if present != set(files):
        extra, missing = sorted(present - set(files)), sorted(set(files) - present)
        raise RunUnreadable(f"files differ from the manifest (extra {extra}, missing {missing})")
    for relpath, expected in files.items():
        if sha256((run_dir / relpath).read_bytes()) != expected:
            raise RunUnreadable(f"{relpath} does not match its checksum")
    try:
        rows = json.loads((run_dir / INDEX).read_bytes())["data"]
    except (ValueError, KeyError, TypeError):
        raise RunUnreadable("files/index.json is not a file list") from None
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) and valid_file_id(row.get("id")) for row in rows
    ):
        raise RunUnreadable("files/index.json lists an invalid file id")
    listed = {f"files/{row['id']}" for row in rows}
    if listed != set(files) - {STATE, ITEMS, INDEX}:
        raise RunUnreadable("files/index.json does not match the run's files")


def load_run(root: Path, tenant_id: str, week: str) -> SavedRun:
    """One run, verified. Raises RunUnreadable; never serves a partial check."""
    if not valid_tenant(tenant_id) or not valid_week(week):
        raise RunUnreadable("not a tenant or week this layout accepts")
    saved = SavedRun(Path(root), tenant_id, week)
    _verify(saved)
    return saved


def scan_run(root: Path, tenant_id: str, week: str) -> SavedRun:
    """One run as found: verified, or marked unreadable with the reason."""
    try:
        return load_run(root, tenant_id, week)
    except RunUnreadable as exc:
        saved = SavedRun(Path(root), tenant_id, week, problem=str(exc))
        try:
            manifest = json.loads((saved.path / MANIFEST).read_bytes())
            saved.manifest = manifest if isinstance(manifest, dict) else {}
        except (OSError, ValueError):
            pass
        return saved


@dataclass
class SavedTenant:
    root: Path
    tenant_id: str
    name: str
    fixture: bool
    #: Newest first by ISO week.
    runs: list[SavedRun]

    def newest_readable(self) -> SavedRun | None:
        return next((r for r in self.runs if r.readable), None)


def scan(root: Path) -> list[SavedTenant]:
    """Every tenant directory under `root` with its runs, newest week first.

    Anything that does not match the layout's names (dot-directories left by an
    interrupted write, stray files, symlinks) is not a tenant or a run and is
    skipped. A tenant whose `tenant.json` is missing or names another tenant is
    skipped too: readiness takes `tenant_id` from it (rule 10).
    """
    root = Path(os.path.expanduser(root))
    tenants = []
    if not root.is_dir():
        return tenants
    for tenant_dir in sorted(root.iterdir()):
        if tenant_dir.is_symlink() or not tenant_dir.is_dir() or not valid_tenant(tenant_dir.name):
            continue
        try:
            meta = json.loads((tenant_dir / TENANT).read_bytes())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("tenant_id") != tenant_dir.name:
            continue
        runs = [
            scan_run(root, tenant_dir.name, week_dir.name)
            for week_dir in sorted(tenant_dir.iterdir(), reverse=True)
            if valid_week(week_dir.name) and not week_dir.is_symlink() and week_dir.is_dir()
        ]
        tenants.append(
            SavedTenant(
                root,
                tenant_dir.name,
                str(meta.get("name") or tenant_dir.name),
                meta.get("fixture") is True,
                runs,
            )
        )
    return tenants

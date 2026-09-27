"""Saved Iris runs: the layout, the integrity check and the committed synthetic fixture.

docs/iris/STANDALONE_VIEWER.md, section 3. ``omnigent/airbrx/iris/fixtures/runs``
is exporter output over synthetic session records
(``scripts/iris/make_fixture_runs.py``). This module fails when the committed
tree drifts from what the generator produces. Regenerate with::

    IRIS_WRITE_FIXTURES=1 uv run pytest tests/airbrx/test_iris_runs.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from omnigent.airbrx.iris import runs
from omnigent.airbrx.iris.runs import Run, RunUnreadable, SavedFile

REPO = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO / "omnigent/airbrx/iris/fixtures/runs"


def load_generator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "make_fixture_runs", REPO / "scripts/iris/make_fixture_runs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = load_generator()


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --- The committed fixture ----------------------------------------------------------


def test_the_committed_fixture_is_what_the_generator_produces(tmp_path: Path) -> None:
    if os.environ.get("IRIS_WRITE_FIXTURES") == "1":
        GENERATOR.build(FIXTURE_ROOT)
    produced = tmp_path / "runs"
    GENERATOR.build(produced)
    assert FIXTURE_ROOT.is_dir(), "Generate the fixture: IRIS_WRITE_FIXTURES=1, see this module."
    committed, fresh = tree(FIXTURE_ROOT), tree(produced)
    assert sorted(committed) == sorted(fresh), "Regenerate the fixture (see this module)."
    drifted = [path for path in committed if committed[path] != fresh[path]]
    assert not drifted, f"Regenerate the fixture (see this module); drifted: {drifted}"


def fixture() -> dict[str, runs.SavedTenant]:
    return {t.tenant_id: t for t in runs.scan(FIXTURE_ROOT)}


def run(tenant: str, week: str) -> runs.SavedRun:
    return runs.load_run(FIXTURE_ROOT, tenant, week)


def test_the_fixture_holds_the_contracts_runs_and_all_are_readable() -> None:
    tenants = fixture()
    assert {t: [r.week for r in s.runs] for t, s in tenants.items()} == {
        "fixture-iris": ["2026-W39", "2026-W38", "2026-W37", "2026-W36"],
        "fixture-iris-b": ["2026-W39"],
    }
    for saved in tenants.values():
        assert all(r.readable for r in saved.runs), [r.problem for r in saved.runs]


def test_every_committed_run_is_synthetic_and_says_so() -> None:
    """Rule V4 and V2 rule 5: invented data only, and the chip shows on every tab."""
    tenants = fixture()
    assert tenants
    for tenant_id, saved in tenants.items():
        assert tenant_id.startswith("fixture-")
        assert saved.fixture is True
        for saved_run in saved.runs:
            assert saved_run.manifest["source"]["kind"] == "synthetic"
            state = saved_run.state()
            reports = [state[k] for k in ("overview", "audit", "investigation", "proposal")]
            for report in filter(None, reports):
                assert report["mode"] == "synthetic fixture"
                assert report["tenant_id"] == tenant_id


def disagreements(m: dict[str, Any]) -> list[str]:
    """The Q19 sums `metricsProblems` in ui/app.js checks, in its words."""
    out = []
    if m["hit_rate_denominator"] != m["requests"]:
        out.append("the hit rate's denominator is not the request count.")
    if m["cache_hits"] + m["cache_misses"] != m["hit_rate_denominator"]:
        out.append("hits and misses do not add up to the hit rate's denominator.")
    if abs(m["hit_rate"] - m["cache_hits"] / m["hit_rate_denominator"]) > 0.0005:
        out.append("the stated hit rate is not its own hits over its own denominator.")
    if m["period_complete"] is True and m["covered_days"] != m["requested_days"]:
        out.append("the period is marked complete, but not every requested day is covered.")
    return out


def test_the_fixture_proves_what_section_3_6_says_it_proves() -> None:
    w36 = run("fixture-iris", "2026-W36").state()
    assert disagreements(w36["overview"]["metrics"]) == [
        "hits and misses do not add up to the hit rate's denominator."
    ]

    w37 = run("fixture-iris", "2026-W37").state()
    assert not disagreements(w37["overview"]["metrics"])
    assert w37["investigation"] is None and w37["proposal"] is None

    w38 = run("fixture-iris", "2026-W38").state()
    assert not disagreements(w38["overview"]["metrics"])
    assert w38["investigation"] is not None
    assert w38["proposal"]["proposal_status"] == "validated"
    assert w38["rules"], "Rules needs the rule-effectiveness rows"

    partial = run("fixture-iris", "2026-W39")
    metrics = partial.state()["overview"]["metrics"]
    assert (metrics["covered_days"], metrics["requested_days"]) == (3, 7)
    assert metrics["period_complete"] is False
    assert not disagreements(metrics), "a partial week is never refused for being partial"
    assert partial.manifest["period"] == {
        "start_date": "2026-09-20",
        "end_date": "2026-09-23",
        "covered_days": 3,
        "requested_days": 7,
        "period_complete": False,
    }

    other = run("fixture-iris-b", "2026-W39").state()
    assert not disagreements(other["overview"]["metrics"])
    assert other["overview"]["metrics"]["period_complete"] is True


def test_accounts_ranks_b_and_quarantines_the_partial_week() -> None:
    """The newest readable run per tenant, through the pinned `iris.account.rank`."""
    from omnigent.airbrx.iris.package import source_root

    source_root()
    from iris.account import rank

    tenants, captures = [], {}
    for saved in fixture().values():
        newest = saved.newest_readable()
        state = newest.state()
        tenants.append({"tenant_id": saved.tenant_id, "name": saved.name, "note": None})
        captures[saved.tenant_id] = {
            "tenant_id": state["overview"]["tenant_id"],
            "metrics": state["overview"]["metrics"],
            "captured_at": state["captured_at"],
        }
    ranked = rank(tenants, captures, now=GENERATOR.EXPORTED_AT)
    assert [r["tenant_id"] for r in ranked["ranked"]] == ["fixture-iris-b"]
    assert [(q["tenant_id"], q["reason"]) for q in ranked["quarantined"]] == [
        ("fixture-iris", "incomplete_period")
    ]


def test_every_evidence_link_in_every_run_resolves() -> None:
    for saved in fixture().values():
        for saved_run in saved.runs:
            state = saved_run.state()
            reports = [
                state[k] for k in ("overview", "audit", "investigation", "proposal") if state[k]
            ]
            known = {e["id"] for r in reports for e in r.get("evidence", [])}
            linked = {i for r in reports for f in r.get("findings", []) for i in f["evidence_ids"]}
            assert linked <= known, (saved_run.week, sorted(linked - known))


def test_saved_chat_is_messages_only_and_keeps_the_refresh_sentence() -> None:
    """Rule 15: no tool items in items.json; the refresh prompt is kept byte-for-byte."""
    from omnigent.airbrx.iris.routes import REFRESH_PROMPT

    for saved in fixture().values():
        for saved_run in saved.runs:
            items = saved_run.items()
            assert items["has_more"] is False
            assert items["data"], saved_run.week
            for item in items["data"]:
                assert item["type"] == "message"
                assert item["role"] in ("user", "assistant")
                assert {p["type"] for p in item["content"]} <= {"input_text", "output_text"}
            texts = [p["text"] for i in items["data"] for p in i["content"]]
            assert REFRESH_PROMPT in texts


def test_downloads_are_the_reports_the_state_stands_behind() -> None:
    w38 = run("fixture-iris", "2026-W38")
    names = sorted(f["filename"] for f in w38.file_index()["data"])
    # overview, audit, investigation and proposal: one report.json and report.md
    # each, and the proposal's proposal.json. Not W36's or W37's reports.
    assert names == ["proposal.json"] + ["report.json"] * 4 + ["report.md"] * 4
    reports = [
        json.loads(w38.file(f["id"]))
        for f in w38.file_index()["data"]
        if f["filename"] == "report.json"
    ]
    state = w38.state()
    assert sorted(r["runId"] for r in reports) == sorted(
        state[k]["runId"] for k in ("overview", "audit", "investigation", "proposal")
    )
    for f in w38.file_index()["data"]:
        assert f["bytes"] == len(w38.file(f["id"]))


@pytest.mark.parametrize(
    ("tenant", "week"),
    [
        ("fixture-iris", "2026-W36"),
        ("fixture-iris", "2026-W37"),
        ("fixture-iris", "2026-W38"),
        ("fixture-iris", "2026-W39"),
        ("fixture-iris-b", "2026-W39"),
    ],
)
def test_build_state_over_the_seeded_items_is_the_saved_state(tenant: str, week: str) -> None:
    """The adapter's builder over the session a run came from answers its state.json.

    SV6 seeds a framed session from these records; if its seeding path is not
    available, this is what covers the adapter side of parity (section 6).
    """
    from omnigent.airbrx.iris.workspace import build_state

    saved = run(tenant, week)
    source = saved.manifest["source"]
    session = next(s for s in GENERATOR.sessions()[tenant] if s.id == source["session_id"])
    ids = [i["id"] for i in session.items]
    seeded = session.items[: ids.index(source["through_item_id"]) + 1]
    state = saved.state()
    built = build_state(
        seeded,
        lambda file_id: json.loads(session.files[file_id]),
        tenant_id=tenant,
        now=state["captured_at"],
    )
    assert json.loads(json.dumps(built)) == state


# --- Layout and path rules -------------------------------------------------------------


@pytest.mark.parametrize(
    "tenant",
    ["fixture-iris", "fixture-a", "00000000-0000-4000-8000-000000000001", "a", "abc-123"],
)
def test_tenant_ids_the_layout_accepts(tenant: str) -> None:
    assert runs.valid_tenant(tenant)


@pytest.mark.parametrize(
    "tenant",
    ["", "..", ".hidden", "Fixture-Iris", "a/b", "a\\b", "-lead", "x" * 64, None, 7],
)
def test_tenant_ids_the_layout_refuses(tenant: object) -> None:
    assert not runs.valid_tenant(tenant)


@pytest.mark.parametrize("week", ["2026-W38", "2027-W01"])
def test_weeks_the_layout_accepts(week: str) -> None:
    assert runs.valid_week(week)


def test_a_trailing_newline_is_not_a_valid_path_part() -> None:
    assert not runs.valid_tenant("fixture-iris\n")
    assert not runs.valid_week("2026-W38\n")
    assert not runs.valid_file_id("file_ab\n")


@pytest.mark.parametrize("week", ["2026-38", "2026-W3", "../2026-W38", "2026-W38/", "2026-w38"])
def test_weeks_the_layout_refuses(week: str) -> None:
    assert not runs.valid_week(week)


@pytest.mark.parametrize("file_id", ["../state.json", "a.json", "a/b", "", "x" * 129, ".."])
def test_file_ids_the_layout_refuses(file_id: str) -> None:
    assert not runs.valid_file_id(file_id)


def test_the_week_is_the_iso_week_of_the_captures_utc_day() -> None:
    # Sunday 2026-09-20 23:59:59Z is still W38; a minute later is W39.
    assert runs.iso_week(1_790_553_599 - 7 * 86_400) == "2026-W38"
    assert runs.iso_week(1_790_553_600 - 7 * 86_400) == "2026-W39"
    # 2027-01-01 is a Friday: ISO week 53 of 2026.
    assert runs.iso_week(1_798_761_600) == "2026-W53"


# --- Serve-time fields (section 3.3) ------------------------------------------------


def test_served_state_recomputes_only_the_two_clock_fields() -> None:
    from omnigent.airbrx.iris.workspace import STALE_AFTER_SECONDS

    assert runs.STALE_AFTER_SECONDS == STALE_AFTER_SECONDS
    state = run("fixture-iris", "2026-W38").state()
    at = state["captured_at"]
    served = runs.served_state(state, at + 300)
    assert (served["cache_age_seconds"], served["stale"]) == (300, False)
    served = runs.served_state(state, at + 300.6)
    assert (served["cache_age_seconds"], served["stale"]) == (301, True)
    assert runs.served_state(state, at - 50)["cache_age_seconds"] == 0
    rest = {k: v for k, v in served.items() if k not in ("cache_age_seconds", "stale")}
    assert rest == {k: v for k, v in state.items() if k not in ("cache_age_seconds", "stale")}


# --- Integrity (rule V3) -------------------------------------------------------------------


def a_run(tenant: str = "fixture-iris", at: float = 1_789_863_660.0, **source: Any) -> Run:
    state = run("fixture-iris", "2026-W38").state()
    state = {
        **state,
        "captured_at": at,
        "overview": {**state["overview"], "tenant_id": tenant},
    }
    return Run(
        tenant_id=tenant,
        state=state,
        items=[
            {
                "id": "m1",
                "type": "message",
                "role": "user",
                "status": "completed",
                "content": [{"type": "input_text", "text": "hello"}],
                "created_at": at,
            }
        ],
        files=[SavedFile("file_abc", "report.md", b"# report\n")],
        source={"kind": "synthetic", "session_id": "s", **source},
    )


def written(tmp_path: Path) -> runs.SavedRun:
    runs.write_run(tmp_path, a_run(), name="Fixture", fixture=True, exported_at=1.0)
    return runs.load_run(tmp_path, "fixture-iris", "2026-W38")


def test_a_written_run_verifies_and_reads_back(tmp_path: Path) -> None:
    saved = written(tmp_path)
    assert saved.readable
    assert saved.file("file_abc") == b"# report\n"
    assert saved.file_index() == {
        "data": [{"id": "file_abc", "filename": "report.md", "bytes": 9}]
    }
    assert json.loads((tmp_path / "fixture-iris/tenant.json").read_text()) == {
        "tenant_id": "fixture-iris",
        "name": "Fixture",
        "fixture": True,
    }
    assert saved.week_entry() == {
        "week": "2026-W38",
        "captured_at": 1_789_863_660.0,
        "start_date": "2026-09-13",
        "end_date": "2026-09-20",
        "period_complete": True,
        "covered_days": 7,
        "requested_days": 7,
        "readable": True,
    }
    # Nothing left behind by the staging directory.
    assert sorted(p.name for p in (tmp_path / "fixture-iris").iterdir()) == [
        "2026-W38",
        "tenant.json",
    ]


def swap_for_symlink(path: Path) -> None:
    path.unlink()
    path.symlink_to("/etc/hosts")


@pytest.mark.parametrize(
    "tamper",
    [
        lambda d: (d / "state.json").write_text("{}"),
        lambda d: (d / "files/file_abc").write_bytes(b"changed"),
        lambda d: (d / "files/extra").write_bytes(b"not in the manifest"),
        lambda d: (d / "items.json").unlink(),
        lambda d: (d / "manifest.json").write_text("not json"),
        lambda d: swap_for_symlink(d / "files/file_abc"),
    ],
    ids=["state", "file", "extra", "missing", "manifest", "symlink"],
)
def test_a_tampered_run_is_listed_unreadable_and_never_served(tmp_path: Path, tamper) -> None:
    written(tmp_path)
    tamper(tmp_path / "fixture-iris/2026-W38")
    with pytest.raises(RunUnreadable):
        runs.load_run(tmp_path, "fixture-iris", "2026-W38")
    [saved] = runs.scan(tmp_path)
    [listed] = saved.runs
    assert listed.readable is False and listed.problem
    assert saved.newest_readable() is None
    with pytest.raises(RunUnreadable):
        listed.state()


def test_a_symlink_to_identical_bytes_is_still_unreadable(tmp_path: Path) -> None:
    """The checksum alone would pass it; the symlink rule is what refuses it (rule V3)."""
    saved = written(tmp_path)
    original = saved.path / "files/file_abc"
    copy = tmp_path / "outside-copy"
    copy.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(copy)
    with pytest.raises(RunUnreadable, match="symlink"):
        runs.load_run(tmp_path, "fixture-iris", "2026-W38")


@pytest.mark.parametrize(
    "index",
    [b"not json", b'{"data": [1]}', b'{"data": "x"}', b"[]", b'{"data": [{"id": null}]}'],
    ids=["not-json", "row-not-object", "data-not-list", "not-object", "id-null"],
)
def test_a_malformed_index_with_a_good_checksum_is_unreadable_not_a_crash(
    tmp_path: Path, index: bytes
) -> None:
    saved = written(tmp_path)
    (saved.path / "files/index.json").write_bytes(index)
    manifest = json.loads((saved.path / "manifest.json").read_text())
    manifest["files"]["files/index.json"] = runs.sha256(index)
    (saved.path / "manifest.json").write_text(json.dumps(manifest))
    [tenant] = runs.scan(tmp_path)
    [listed] = tenant.runs
    assert listed.readable is False and listed.problem
    # And the next export replaces it rather than crashing on it.
    assert runs.write_run(tmp_path, a_run(), name="F", fixture=True, exported_at=9.0) == "replaced"


def test_a_finder_ds_store_does_not_make_a_run_unreadable(tmp_path: Path) -> None:
    """Abram browses runs in Finder, which drops .DS_Store files; they are not run content."""
    saved = written(tmp_path)
    (saved.path / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (saved.path / "files/.DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    again = runs.load_run(tmp_path, "fixture-iris", "2026-W38")
    assert again.readable
    with pytest.raises(RunUnreadable):
        again.read(".DS_Store")


def test_a_file_changed_after_loading_is_refused_on_read(tmp_path: Path) -> None:
    saved = written(tmp_path)
    (saved.path / "files/file_abc").write_bytes(b"changed after the check")
    with pytest.raises(RunUnreadable):
        saved.file("file_abc")


def test_a_manifest_naming_another_week_is_unreadable(tmp_path: Path) -> None:
    written(tmp_path)
    (tmp_path / "fixture-iris/2026-W38").rename(tmp_path / "fixture-iris/2026-W37")
    [saved] = runs.scan(tmp_path)
    assert [r.readable for r in saved.runs] == [False]


def test_scan_skips_what_is_not_the_layout(tmp_path: Path) -> None:
    written(tmp_path)
    (tmp_path / "fixture-iris/.2026-W38-abc").mkdir()
    (tmp_path / "fixture-iris/notes").mkdir()
    (tmp_path / "Not-A-Tenant").mkdir()
    (tmp_path / "fixture-link").symlink_to(tmp_path / "fixture-iris")
    (tmp_path / "fixture-lies").mkdir()
    (tmp_path / "fixture-lies/tenant.json").write_text('{"tenant_id": "fixture-iris"}')
    assert [(t.tenant_id, [r.week for r in t.runs]) for t in runs.scan(tmp_path)] == [
        ("fixture-iris", ["2026-W38"])
    ]


# --- Writing ---------------------------------------------------------------------------------


def test_a_run_is_replaced_only_by_a_newer_capture_or_force(tmp_path: Path) -> None:
    first = a_run(at=1_789_863_660.0)
    assert runs.write_run(tmp_path, first, name="F", fixture=True, exported_at=1.0) == "wrote"
    older = a_run(at=1_789_863_000.0)
    assert runs.write_run(tmp_path, older, name="F", fixture=True, exported_at=2.0) == "kept"
    assert runs.load_run(tmp_path, "fixture-iris", "2026-W38").manifest["captured_at"] == (
        1_789_863_660.0
    )
    newer = a_run(at=1_789_864_000.0)
    assert runs.write_run(tmp_path, newer, name="F", fixture=True, exported_at=3.0) == "replaced"
    assert runs.write_run(
        tmp_path, older, name="F", fixture=True, exported_at=4.0, force=True
    ) == ("replaced")
    saved = runs.load_run(tmp_path, "fixture-iris", "2026-W38")
    assert saved.manifest["captured_at"] == 1_789_863_000.0
    assert sorted(p.name for p in (tmp_path / "fixture-iris").iterdir()) == [
        "2026-W38",
        "tenant.json",
    ]


def test_an_unreadable_run_is_replaced_by_the_next_export(tmp_path: Path) -> None:
    written(tmp_path)
    (tmp_path / "fixture-iris/2026-W38/state.json").write_text("{}")
    again = a_run()
    assert runs.write_run(tmp_path, again, name="F", fixture=True, exported_at=5.0) == "replaced"
    assert runs.load_run(tmp_path, "fixture-iris", "2026-W38").readable


def git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_a_real_run_is_refused_inside_a_git_worktree(tmp_path: Path) -> None:
    """Rule V4: real tenant evidence never lands in a repository, whoever calls the writer."""
    repo = git_repo(tmp_path / "repo")
    real = a_run(tenant="00000000-0000-4000-8000-000000000001", kind="omnigent-session")
    with pytest.raises(runs.RefusedOutput):
        runs.write_run(repo / "deep/runs", real, name="T", fixture=False, exported_at=1.0)
    # A synthetic run marked non-fixture is refused too; only the fixture may be committed.
    with pytest.raises(runs.RefusedOutput):
        runs.write_run(repo / "runs", a_run(), name="T", fixture=False, exported_at=1.0)
    assert not (repo / "deep").exists() and not (repo / "runs").exists()
    # A symlink out of the repository is judged by where it points.
    (tmp_path / "outside").mkdir()
    (repo / "link").symlink_to(tmp_path / "outside")
    runs.write_run(repo / "link", real, name="T", fixture=False, exported_at=1.0)
    (tmp_path / "elsewhere").symlink_to(repo)
    with pytest.raises(runs.RefusedOutput):
        runs.write_run(tmp_path / "elsewhere/runs", real, name="T", fixture=False, exported_at=1.0)


def test_a_symlinked_tenant_directory_is_refused(tmp_path: Path) -> None:
    """The reviewer's repro: <out>/<tenant> is a symlink into a git repository (rule V4)."""
    repo = git_repo(tmp_path / "repo")
    planted = repo / "sub/planted"
    planted.mkdir(parents=True)
    out = tmp_path / "runs"
    out.mkdir()
    (out / "fixture-iris").symlink_to(planted)
    real = a_run(kind="omnigent-session")
    with pytest.raises(runs.RefusedOutput):
        runs.write_run(out, real, name="T", fixture=False, exported_at=1.0)
    assert not any(planted.iterdir())
    # A symlinked tenant directory is refused wherever it points, even for the fixture.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (out / "fixture-iris").unlink()
    (out / "fixture-iris").symlink_to(elsewhere)
    with pytest.raises(runs.RefusedOutput):
        runs.write_run(out, a_run(), name="T", fixture=True, exported_at=1.0)
    assert not any(elsewhere.iterdir())


@pytest.mark.parametrize("repo_at", ["fixture-iris", "fixture-iris/2026-W38"])
def test_a_tenant_or_run_directory_that_is_itself_a_repository_is_refused(
    tmp_path: Path, repo_at: str
) -> None:
    """The root is outside any repo, but a directory below it is a git checkout (rule V4)."""
    out = tmp_path / "runs"
    git_repo(out / repo_at)
    real = a_run(kind="omnigent-session")
    with pytest.raises(runs.RefusedOutput, match="git worktree"):
        runs.write_run(out, real, name="T", fixture=False, exported_at=1.0)
    assert sorted(p.name for p in (out / repo_at).iterdir()) == [".git"]


def test_a_symlinked_run_directory_is_refused(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "runs/fixture-iris").mkdir(parents=True)
    (tmp_path / "runs/fixture-iris/2026-W38").symlink_to(elsewhere)
    with pytest.raises(runs.RefusedOutput):
        runs.write_run(tmp_path / "runs", a_run(), name="T", fixture=True, exported_at=1.0)
    assert not any(elsewhere.iterdir())


def test_the_in_repository_exemption_is_for_fixture_tenants_only(tmp_path: Path) -> None:
    """A synthetic run marked fixture but named like a real tenant is still refused in a repo."""
    repo = git_repo(tmp_path / "repo")
    uuid_tenant = a_run(tenant="00000000-0000-4000-8000-000000000001")
    with pytest.raises(runs.RefusedOutput):
        runs.write_run(repo / "runs", uuid_tenant, name="T", fixture=True, exported_at=1.0)
    assert not (repo / "runs").exists()
    assert runs.write_run(repo / "runs", a_run(), name="F", fixture=True, exported_at=1.0) == (
        "wrote"
    )


def test_a_failed_first_write_leaves_no_tenant_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = Path.write_bytes
    calls = {"n": 0}

    def failing(self: Path, data: bytes) -> int:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing)
    with pytest.raises(OSError):
        runs.write_run(tmp_path, a_run(), name="F", fixture=True, exported_at=1.0)
    monkeypatch.undo()
    assert runs.scan(tmp_path) == []
    assert not (tmp_path / "fixture-iris").exists()


def test_the_writer_refuses_what_the_layout_cannot_hold(tmp_path: Path) -> None:
    bad_file = a_run()
    bad_file.files = [SavedFile("../escape", "report.md", b"x")]
    tool_item = a_run()
    tool_item.items = [{"type": "function_call", "name": "iris_overview"}]
    other_tenant = a_run()
    other_tenant.state["overview"]["tenant_id"] = "fixture-other"
    unlisted_name = a_run()
    unlisted_name.files = [SavedFile("file_x", "secrets.txt", b"x")]
    for bad in (bad_file, tool_item, other_tenant, unlisted_name):
        with pytest.raises(ValueError):
            runs.write_run(tmp_path, bad, name="F", fixture=True, exported_at=1.0)
    assert not any(tmp_path.iterdir())


def test_runs_imports_only_the_stdlib() -> None:
    """The viewer imports runs.py; it must not drag in the server (section 1)."""
    code = (
        "import sys, omnigent.airbrx.iris.runs;"
        "bad = [m for m in sys.modules if m.startswith(('omnigent.server', 'fastapi', "
        "'omnigent.airbrx.iris.routes', 'omnigent.airbrx.iris.workspace'))];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

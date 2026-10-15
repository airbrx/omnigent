"""Tests for the user-scoped self-restarting host service installer.

The installer shells out to systemctl/launchctl/loginctl, so tests inject a
fake command-runner and assert on (a) the rendered unit/plist text and (b) the
commands that would be issued — no real init system is touched.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from omnigent.host import service


class FakeRunner:
    """Records issued commands; returns canned stdout for probe calls."""

    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.outputs = outputs or {}

    def __call__(self, args, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        stdout = ""
        for token, value in self.outputs.items():
            if token in args:
                stdout = value
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def issued(self, *tokens: str) -> bool:
        """True if some recorded call contains all *tokens*."""
        return any(all(t in call for t in tokens) for call in self.calls)


@pytest.fixture
def cfg() -> service.HostServiceConfig:
    return service.HostServiceConfig(
        server_url="https://example.databricksapps.com",
        exec_path="/usr/local/bin/omnigent",
        environment={"DATABRICKS_HOST": "https://x", "OMNIGENT_DATA_DIR": "/data"},
    )


@pytest.fixture
def as_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "IS_LINUX", True)
    monkeypatch.setattr(service, "IS_DARWIN", False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def as_darwin(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "IS_LINUX", False)
    monkeypatch.setattr(service, "IS_DARWIN", True)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ── build_exec_args ────────────────────────────────────────────────────────


def test_exec_args_always_non_interactive(cfg):
    args = service.build_exec_args(cfg)
    assert args[:2] == ["/usr/local/bin/omnigent", "host"]
    assert "--non-interactive" in args
    assert "--server" in args and "https://example.databricksapps.com" in args
    assert "--auto-upgrade" not in args


def test_exec_args_shared_with_workroot(cfg):
    shared = service.HostServiceConfig(
        server_url=cfg.server_url,
        exec_path=cfg.exec_path,
        shared=True,
        workroot="/srv/work",
        auto_upgrade=True,
    )
    args = service.build_exec_args(shared)
    assert "--auto-upgrade" in args
    assert args[args.index("--workroot") + 1] == "/srv/work"
    assert "--shared" in args


# ── validation ─────────────────────────────────────────────────────────────


def test_shared_requires_workroot(as_linux, cfg):
    bad = service.HostServiceConfig(
        server_url=cfg.server_url, exec_path=cfg.exec_path, shared=True, workroot=None
    )
    with pytest.raises(service.HostServiceError, match="workroot"):
        service.install_service(bad, runner=FakeRunner())


def test_server_required(as_linux, cfg):
    bad = service.HostServiceConfig(server_url="", exec_path=cfg.exec_path)
    with pytest.raises(service.HostServiceError, match="server"):
        service.install_service(bad, runner=FakeRunner())


def test_unsupported_platform(monkeypatch, cfg):
    monkeypatch.setattr(service, "IS_LINUX", False)
    monkeypatch.setattr(service, "IS_DARWIN", False)
    with pytest.raises(service.HostServiceError, match=r"Linux.*macOS|Windows"):
        service.install_service(cfg, runner=FakeRunner())


# ── systemd rendering ──────────────────────────────────────────────────────


def test_systemd_unit_content(cfg):
    unit = service.render_systemd_unit(cfg)
    assert "Restart=always" in unit
    assert "RestartSec=5" in unit
    assert "ExecStart=/usr/local/bin/omnigent host --server" in unit
    assert "--non-interactive" in unit
    assert 'Environment="DATABRICKS_HOST=https://x"' in unit
    assert 'Environment="OMNIGENT_DATA_DIR=/data"' in unit
    assert "WantedBy=default.target" in unit


def test_systemd_env_escaping():
    cfg = service.HostServiceConfig(
        server_url="https://x",
        exec_path="/bin/omnigent",
        environment={"Q": 'a"b\\c'},
    )
    unit = service.render_systemd_unit(cfg)
    assert 'Environment="Q=a\\"b\\\\c"' in unit


def test_systemd_workingdir_is_workroot_when_shared():
    cfg = service.HostServiceConfig(
        server_url="https://x",
        exec_path="/bin/omnigent",
        shared=True,
        workroot="/srv/work space",
    )
    unit = service.render_systemd_unit(cfg)
    assert "WorkingDirectory=/srv/work space" in unit
    # a workroot with a space still lands in ExecStart
    assert "--workroot /srv/work space" in unit


# ── launchd rendering ──────────────────────────────────────────────────────


def test_launchd_plist_content(cfg):
    xml = service.render_launchd_plist(cfg)
    parsed = plistlib.loads(xml.encode("utf-8"))
    assert parsed["Label"] == service.LAUNCHD_LABEL
    assert parsed["KeepAlive"] is True
    assert parsed["RunAtLoad"] is True
    assert parsed["ThrottleInterval"] == 10  # floor even though restart_sec=5
    assert parsed["ProgramArguments"][:2] == ["/usr/local/bin/omnigent", "host"]
    assert parsed["EnvironmentVariables"]["OMNIGENT_DATA_DIR"] == "/data"


# ── install: linux ─────────────────────────────────────────────────────────


def test_install_linux_writes_and_enables(as_linux, cfg):
    runner = FakeRunner()
    result = service.install_service(cfg, runner=runner)
    unit = service.systemd_unit_path()
    assert unit.exists()
    assert oct(unit.stat().st_mode)[-3:] == "600"
    assert runner.issued("systemctl", "--user", "daemon-reload")
    assert runner.issued("systemctl", "--user", "enable", service.SYSTEMD_UNIT_NAME)
    assert runner.issued("systemctl", "--user", "restart", service.SYSTEMD_UNIT_NAME)
    assert runner.issued("loginctl", "enable-linger")
    assert result.linger_enabled is True
    assert service._linger_marker_path().exists()


def test_install_linux_skips_linger_when_already_on(as_linux, cfg):
    runner = FakeRunner(outputs={"show-user": "Linger=yes\n"})
    result = service.install_service(cfg, runner=runner)
    assert result.linger_enabled is False
    assert not runner.issued("loginctl", "enable-linger")
    assert not service._linger_marker_path().exists()


def test_install_linux_no_linger_flag(as_linux, cfg):
    runner = FakeRunner()
    result = service.install_service(cfg, runner=runner, enable_linger=False)
    assert result.linger_enabled is False
    assert not runner.issued("loginctl", "enable-linger")


# ── install: darwin ────────────────────────────────────────────────────────


def test_install_darwin_bootstraps(as_darwin, cfg):
    runner = FakeRunner()
    result = service.install_service(cfg, runner=runner)
    assert service.launchd_plist_path().exists()
    assert runner.issued("launchctl", "bootout")  # idempotent pre-clean
    assert runner.issued("launchctl", "bootstrap")
    assert result.linger_enabled is False


# ── uninstall ──────────────────────────────────────────────────────────────


def test_uninstall_linux_removes_and_drops_our_linger(as_linux, cfg):
    service.install_service(cfg, runner=FakeRunner())
    assert service.systemd_unit_path().exists()
    runner = FakeRunner()
    removed = service.uninstall_service(runner=runner)
    assert removed is True
    assert not service.systemd_unit_path().exists()
    assert runner.issued("systemctl", "--user", "disable", "--now")
    assert runner.issued("loginctl", "disable-linger")  # marker was present
    assert not service._linger_marker_path().exists()


def test_uninstall_linux_keeps_foreign_linger(as_linux, cfg):
    # install without linger → no marker → uninstall must NOT touch linger
    service.install_service(cfg, runner=FakeRunner(), enable_linger=False)
    runner = FakeRunner()
    service.uninstall_service(runner=runner)
    assert not runner.issued("loginctl", "disable-linger")


def test_uninstall_when_nothing_installed(as_linux):
    runner = FakeRunner()
    assert service.uninstall_service(runner=runner) is False
    assert runner.calls == []


def test_uninstall_darwin(as_darwin, cfg):
    service.install_service(cfg, runner=FakeRunner())
    runner = FakeRunner()
    assert service.uninstall_service(runner=runner) is True
    assert not service.launchd_plist_path().exists()
    assert runner.issued("launchctl", "bootout")


# ── binary resolution ──────────────────────────────────────────────────────


def test_resolve_omnigent_bin(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: "/opt/bin/omnigent")
    assert service.resolve_omnigent_bin() == "/opt/bin/omnigent"


def test_resolve_omnigent_bin_missing(monkeypatch):
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    monkeypatch.setattr(service.sys, "argv", ["python"])
    with pytest.raises(service.HostServiceError, match="Could not locate"):
        service.resolve_omnigent_bin()


# ── upstream: enable/disable user host service (new-style API) ─────────────


def _capture_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        returncode = 1 if args[:2] == ["launchctl", "print"] else 0
        return subprocess.CompletedProcess(args, returncode, "", "")

    monkeypatch.setattr(service.subprocess, "run", _run)
    monkeypatch.setattr(service, "_record_service", lambda installed: None)
    monkeypatch.setattr(service, "_forget_service", lambda installed: None)
    return calls


def test_enable_launchd_user_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service.os, "getuid", lambda: 501)
    monkeypatch.setattr(service.sys, "executable", "/opt/omnigent/bin/python")
    calls = _capture_runs(monkeypatch)

    installed = service.enable_user_host_service(
        "https://example.com",
        environment={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    payload = plistlib.loads(installed.path.read_bytes())
    assert installed.path == tmp_path / "Library/LaunchAgents/ai.omnigent.host.plist"
    assert payload["Label"] == "ai.omnigent.host"
    assert payload["ProgramArguments"] == [
        "/opt/omnigent/bin/python",
        "-m",
        "omnigent.host.service_entry",
        "--server",
        "https://example.com",
    ]
    assert payload["EnvironmentVariables"]["PATH"] == "/usr/bin:/bin"
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert "ProcessType" not in payload
    assert calls == [
        ["launchctl", "bootout", "gui/501/ai.omnigent.host"],
        [
            "launchctl",
            "bootstrap",
            "gui/501",
            str(installed.path),
        ],
    ]
    assert installed.path.stat().st_mode & 0o777 == 0o600


def test_disable_launchd_user_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service.os, "getuid", lambda: 502)
    path = tmp_path / "Library/LaunchAgents/ai.omnigent.host.plist"
    path.parent.mkdir(parents=True)
    path.write_text("old")
    calls = _capture_runs(monkeypatch)

    removed = service.disable_user_host_service()

    assert removed.path == path
    assert not path.exists()
    assert calls == [
        ["launchctl", "bootout", "gui/502/ai.omnigent.host"],
        ["launchctl", "print", "gui/502/ai.omnigent.host"],
    ]


def test_disable_launchd_tolerates_stale_print_during_async_unload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale ``launchctl print`` right after ``bootout`` must not leak the plist.

    ``bootout`` unloads asynchronously, so ``print`` can still report the job
    for a moment; disable must retry instead of aborting before the unlink.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service.os, "getuid", lambda: 502)
    monkeypatch.setattr(service, "_LAUNCHD_UNLOAD_POLL_INTERVAL", 0.0)
    path = tmp_path / "Library/LaunchAgents/ai.omnigent.host.plist"
    path.parent.mkdir(parents=True)
    path.write_text("old")
    calls: list[list[str]] = []
    print_answers = iter([0, 113])  # stale window, then the job is gone

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        returncode = next(print_answers) if args[:2] == ["launchctl", "print"] else 0
        return subprocess.CompletedProcess(args, returncode, "", "")

    monkeypatch.setattr(service.subprocess, "run", _run)
    monkeypatch.setattr(service, "_forget_service", lambda installed: None)

    removed = service.disable_user_host_service()

    assert removed.path == path
    assert not path.exists()
    assert calls == [
        ["launchctl", "bootout", "gui/502/ai.omnigent.host"],
        ["launchctl", "print", "gui/502/ai.omnigent.host"],
        ["launchctl", "print", "gui/502/ai.omnigent.host"],
    ]


def test_disable_launchd_removes_plist_even_when_job_never_unloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuinely stuck job still must not retain the RunAtLoad plist.

    The error is surfaced, but only after the definition is removed — a
    surviving plist would silently restore the service at the next login.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service.os, "getuid", lambda: 502)
    monkeypatch.setattr(service, "_LAUNCHD_UNLOAD_TIMEOUT", 0.0)
    monkeypatch.setattr(service, "_LAUNCHD_UNLOAD_POLL_INTERVAL", 0.0)
    path = tmp_path / "Library/LaunchAgents/ai.omnigent.host.plist"
    path.parent.mkdir(parents=True)
    path.write_text("old")
    forgotten: list[service.HostService] = []

    def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(service.subprocess, "run", _run)
    monkeypatch.setattr(service, "_forget_service", forgotten.append)

    with pytest.raises(service.HostServiceError, match="still running"):
        service.disable_user_host_service()

    assert not path.exists()
    assert forgotten, "the uninstall ledger entry must still be cleared"


def test_enable_systemd_user_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    monkeypatch.setattr(service.sys, "executable", "/opt/omnigent/bin/python")
    calls = _capture_runs(monkeypatch)

    installed = service.enable_user_host_service(
        None,
        environment={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )

    unit = installed.path.read_text()
    assert installed.path == tmp_path / "xdg/systemd/user/omnigent-host.service"
    assert 'Environment="HOME=' in unit
    assert (
        'ExecStart="/opt/omnigent/bin/python" "-m" "omnigent.host.service_entry" "--local"'
    ) in unit
    assert "Restart=on-failure" in unit
    assert "RestartPreventExitStatus=78 143" in unit
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "omnigent-host.service"],
    ]


def test_systemd_unit_escapes_specifiers_and_literal_dollars() -> None:
    unit = service._systemd_unit(
        command=["/opt/$tools/python", "--server", "https://example.com/%h/$target"],
        environment={"CONFIG": "$HOME/%h"},
    ).decode()

    assert 'Environment="CONFIG=$HOME/%%h"' in unit
    assert (
        'ExecStart="/opt/$$tools/python" "--server" "https://example.com/%%h/$$target"'
    ) in unit


def test_disable_systemd_user_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    path = tmp_path / ".config/systemd/user/omnigent-host.service"
    path.parent.mkdir(parents=True)
    path.write_text("old")
    calls = _capture_runs(monkeypatch)

    removed = service.disable_user_host_service()

    assert removed.path == path
    assert not path.exists()
    assert calls == [
        ["systemctl", "--user", "disable", "--now", "omnigent-host.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_host_service_rejects_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.platform, "system", lambda: "Windows")

    with pytest.raises(service.HostServiceError, match="macOS and Linux"):
        service.enable_user_host_service(None, environment={})


def test_service_entry_maps_fatal_host_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnigent.cli import cli
    from omnigent.host import HOST_FATAL_EXIT_CODE, service_entry

    monkeypatch.setattr(sys, "argv", ["service-entry", "--local"])

    def _fatal(**kwargs: object) -> None:
        raise SystemExit(HOST_FATAL_EXIT_CODE)

    monkeypatch.setattr(cli, "main", _fatal)

    assert service_entry.main() == 0

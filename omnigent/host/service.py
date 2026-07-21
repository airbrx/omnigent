"""Install the Omnigent host as a user-scoped, self-restarting service.

The host process (``omnigent host``) already self-heals two failure modes:
tunnel drops (its reconnect loop) and version skew (``--auto-upgrade`` re-execs
in place). The third — the process itself dying — needs a supervisor, because a
dead process cannot restart itself. This module installs that supervisor.

It is deliberately **user-scoped**, never root/system: the host resolves ``$HOME``
for ``~/.claude`` / ``~/.codex`` / ``~/.omnigent`` credentials, so a system unit
would resolve the wrong home and silently break every agent launch.

    ┌────────────┐  install   ┌──────────────────────┐
    │ omnigent   │──────────▶ │ user unit (per OS):   │
    │ host       │            │  systemd --user  (L)  │
    │ install    │            │  launchd Agent   (M)  │
    └────────────┘            └───────────┬──────────┘
                                          │ Restart=always / KeepAlive
                                          ▼
        crash ──▶ supervisor relaunches ``omnigent host`` in <RestartSec>
        clean stop / uninstall ──▶ stays down

Scope (v1, minimal): supervise a *remote* (``--server``) host; capture the
allowlisted daemon env so the service connects with the same environment the
manual host used. NOT handled here: a typed permanent-failure exit code
(a permanently-broken host restart-loops — rare, log-visible, recoverable),
unit-aware ``host stop``/``status``, multi-target units, and local-mode server
ownership. Those are deferred by design.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from omnigent._platform import IS_DARWIN, IS_LINUX

#: systemd user unit / launchd label. Single-target in v1 (multi-target
#: identity is deferred), so a fixed name is safe.
SYSTEMD_UNIT_NAME = "omnigent-host.service"
LAUNCHD_LABEL = "ai.omnigent.host"


class CommandRunner(Protocol):
    """Runs a command and returns the completed process.

    Injected so tests can assert on issued commands without touching a real
    init system.
    """

    def __call__(
        self, args: list[str], *, check: bool = ...
    ) -> subprocess.CompletedProcess[str]: ...


class HostServiceError(Exception):
    """A host-service install/uninstall could not be completed."""


@dataclass(frozen=True)
class HostServiceConfig:
    """What to bake into the supervised unit.

    :param server_url: Remote Omnigent server URL, e.g.
        ``"https://example.databricksapps.com"``. Required — v1 supervises
        remote hosts only.
    :param exec_path: Absolute path to the ``omnigent`` binary the unit runs.
    :param auto_upgrade: Pass ``--auto-upgrade`` to the supervised host.
    :param shared: Pass ``--shared`` (open the host to any authed user).
    :param workroot: Jail dir for a shared host. REQUIRED when ``shared`` — a
        persistent daemon must not default to a surprising cwd.
    :param environment: Allowlisted env to embed (from the CLI's
        ``_build_host_daemon_env``), so the service connects with the same
        environment the manual host used.
    :param restart_sec: Seconds a crashed host waits before relaunch.
    """

    server_url: str
    exec_path: str
    auto_upgrade: bool = False
    shared: bool = False
    workroot: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    restart_sec: int = 5


@dataclass(frozen=True)
class InstallResult:
    """Outcome of an install for user-facing reporting.

    :param unit_path: Path of the written unit/plist.
    :param status_cmd: Command the user can run to inspect the service.
    :param stop_cmd: Command that stops the service (supervisor-aware).
    :param linger_enabled: Whether this install turned on ``enable-linger``.
    """

    unit_path: Path
    status_cmd: str
    stop_cmd: str
    linger_enabled: bool


def _default_runner(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run *args* capturing text output; the injectable default runner."""
    return subprocess.run(args, check=check, capture_output=True, text=True)


def resolve_omnigent_bin() -> str:
    """Absolute path to the ``omnigent`` binary the unit should exec.

    Prefer the console script on PATH (matches how ``--auto-upgrade`` re-execs
    via ``sys.argv[0]``); fall back to the current ``sys.argv[0]`` resolved.

    :returns: An absolute filesystem path.
    :raises HostServiceError: If no ``omnigent`` binary can be resolved.
    """
    found = shutil.which("omnigent")
    if found:
        return str(Path(found).resolve())
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        resolved = Path(argv0).resolve()
        if resolved.exists() and os.path.basename(argv0) in {"omnigent", "omni"}:
            return str(resolved)
    raise HostServiceError(
        "Could not locate the 'omnigent' binary to supervise. Ensure it is on "
        "PATH (e.g. `which omnigent`)."
    )


def systemd_user_dir() -> Path:
    """Directory for the user's systemd units (``~/.config/systemd/user``)."""
    return Path.home() / ".config" / "systemd" / "user"


def systemd_unit_path() -> Path:
    """Path of the systemd user unit file."""
    return systemd_user_dir() / SYSTEMD_UNIT_NAME


def launchd_plist_path() -> Path:
    """Path of the launchd LaunchAgent plist."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _linger_marker_path() -> Path:
    """Marker recording that *we* enabled linger (so uninstall can undo it)."""
    return systemd_user_dir() / ".omnigent-host-linger-set"


def _validate(config: HostServiceConfig) -> None:
    """Reject a config that can't be safely supervised.

    :raises HostServiceError: On an unsupported platform or a shared host
        without an explicit ``--workroot``.
    """
    if not (IS_LINUX or IS_DARWIN):
        raise HostServiceError(
            "`omnigent host install` supports Linux (systemd --user) and macOS "
            "(launchd) only. On Windows, run the host under Task Scheduler "
            "manually."
        )
    if not config.server_url:
        raise HostServiceError(
            "`omnigent host install` requires a remote --server URL "
            "(local-mode installs are not supported in v1)."
        )
    if config.shared and not config.workroot:
        raise HostServiceError(
            "A persistent shared host must name its --workroot explicitly "
            "(a reboot-surviving daemon must not default to the current "
            "directory). Re-run with --shared --workroot <dir>."
        )


def build_exec_args(config: HostServiceConfig) -> list[str]:
    """The ``omnigent host …`` argv the unit runs.

    Always ``--non-interactive`` (no TTY/browser under a supervisor).

    :returns: argv such as ``["/usr/bin/omnigent", "host", "--server", url,
        "--non-interactive", "--auto-upgrade"]``.
    """
    args = [config.exec_path, "host", "--server", config.server_url, "--non-interactive"]
    if config.auto_upgrade:
        args.append("--auto-upgrade")
    if config.shared:
        args.append("--shared")
        if config.workroot:
            args.extend(["--workroot", config.workroot])
    return args


def _working_dir(config: HostServiceConfig) -> str:
    """Pin the service working dir: the shared workroot, else the user's home."""
    return config.workroot if config.shared and config.workroot else str(Path.home())


def _systemd_env_line(key: str, value: str) -> str:
    r"""Render one ``Environment=`` line, escaping ``\`` and ``"`` for systemd."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{key}={escaped}"'


def render_systemd_unit(config: HostServiceConfig) -> str:
    """Render the systemd ``--user`` unit text.

    ``Restart=always`` + ``RestartSec`` is the whole point: a crashed host comes
    back. Env is embedded so the service matches the manual host's environment.
    """
    exec_start = " ".join(build_exec_args(config))
    env_lines = "\n".join(_systemd_env_line(k, v) for k, v in sorted(config.environment.items()))
    env_block = f"{env_lines}\n" if env_lines else ""
    return (
        "[Unit]\n"
        "Description=Omnigent host daemon (self-restarting)\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=always\n"
        f"RestartSec={config.restart_sec}\n"
        f"WorkingDirectory={_working_dir(config)}\n"
        f"{env_block}"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_launchd_plist(config: HostServiceConfig) -> str:
    """Render the launchd LaunchAgent plist XML.

    ``KeepAlive=true`` relaunches the host on death; ``ThrottleInterval`` is
    launchd's minimum respawn spacing (its floor is 10s). ``RunAtLoad`` starts
    it at login / on bootstrap.
    """
    log_dir = Path.home() / "Library" / "Logs"
    plist: dict[str, object] = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": build_exec_args(config),
        "KeepAlive": True,
        "RunAtLoad": True,
        "ThrottleInterval": max(config.restart_sec, 10),
        "WorkingDirectory": _working_dir(config),
        "StandardOutPath": str(log_dir / "omnigent-host.log"),
        "StandardErrorPath": str(log_dir / "omnigent-host.log"),
    }
    if config.environment:
        plist["EnvironmentVariables"] = dict(config.environment)
    return plistlib.dumps(plist).decode("utf-8")


def _write_private(path: Path, text: str) -> None:
    """Write *text* to *path* at mode 0600 (units may embed auth env)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.chmod(path, 0o600)


def _install_linux(
    config: HostServiceConfig, runner: CommandRunner, enable_linger: bool
) -> InstallResult:
    """Write + enable the systemd user unit; optionally enable linger."""
    unit_path = systemd_unit_path()
    _write_private(unit_path, render_systemd_unit(config))
    runner(["systemctl", "--user", "daemon-reload"])
    runner(["systemctl", "--user", "enable", SYSTEMD_UNIT_NAME])
    # restart (not just start) so a re-install applies the new unit immediately.
    runner(["systemctl", "--user", "restart", SYSTEMD_UNIT_NAME])

    linger_enabled = False
    if enable_linger and not _linger_already_on(runner):
        user = _current_user()
        try:
            runner(["loginctl", "enable-linger", user])
            _linger_marker_path().write_text("1\n")
            linger_enabled = True
        except (subprocess.CalledProcessError, OSError):
            # Non-fatal: the unit is installed and runs while logged in; linger
            # only affects survival across logout. Surfaced to the caller.
            linger_enabled = False
    return InstallResult(
        unit_path=unit_path,
        status_cmd=f"systemctl --user status {SYSTEMD_UNIT_NAME}",
        stop_cmd=(
            f"omnigent host uninstall  (or: systemctl --user disable --now {SYSTEMD_UNIT_NAME})"
        ),
        linger_enabled=linger_enabled,
    )


def _install_darwin(config: HostServiceConfig, runner: CommandRunner) -> InstallResult:
    """Write + bootstrap the launchd LaunchAgent."""
    plist_path = launchd_plist_path()
    _write_private(plist_path, render_launchd_plist(config))
    domain = f"gui/{os.getuid()}"
    # bootout first so a re-install cleanly replaces a loaded agent.
    runner(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"], check=False)
    runner(["launchctl", "bootstrap", domain, str(plist_path)])
    return InstallResult(
        unit_path=plist_path,
        status_cmd=f"launchctl print {domain}/{LAUNCHD_LABEL}",
        stop_cmd=f"omnigent host uninstall  (or: launchctl bootout {domain}/{LAUNCHD_LABEL})",
        linger_enabled=False,
    )


def install_service(
    config: HostServiceConfig,
    *,
    runner: CommandRunner = _default_runner,
    enable_linger: bool = True,
) -> InstallResult:
    """Validate, render, write, and start the supervised host service.

    :param config: What to bake into the unit.
    :param runner: Injected command runner (default shells out).
    :param enable_linger: Linux only — run ``loginctl enable-linger`` so the
        service survives logout/reboot.
    :returns: An :class:`InstallResult` for user-facing reporting.
    :raises HostServiceError: On an unsupported platform or invalid config.
    """
    _validate(config)
    if IS_LINUX:
        return _install_linux(config, runner, enable_linger)
    return _install_darwin(config, runner)


def uninstall_service(*, runner: CommandRunner = _default_runner) -> bool:
    """Stop, disable, and remove the supervised host service.

    Drops ``enable-linger`` only if *we* set it (marker present) — the user may
    rely on linger for other services.

    :returns: ``True`` if a unit was present and removed, ``False`` if none.
    :raises HostServiceError: On an unsupported platform.
    """
    if not (IS_LINUX or IS_DARWIN):
        raise HostServiceError("host uninstall supports Linux and macOS only.")
    if IS_LINUX:
        return _uninstall_linux(runner)
    return _uninstall_darwin(runner)


def _uninstall_linux(runner: CommandRunner) -> bool:
    unit_path = systemd_unit_path()
    if not unit_path.exists():
        return False
    runner(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME], check=False)
    unit_path.unlink(missing_ok=True)
    runner(["systemctl", "--user", "daemon-reload"], check=False)
    marker = _linger_marker_path()
    if marker.exists():
        runner(["loginctl", "disable-linger", _current_user()], check=False)
        marker.unlink(missing_ok=True)
    return True


def _uninstall_darwin(runner: CommandRunner) -> bool:
    plist_path = launchd_plist_path()
    if not plist_path.exists():
        return False
    runner(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], check=False)
    plist_path.unlink(missing_ok=True)
    return True


def _current_user() -> str:
    """Login name for ``loginctl`` linger commands."""
    import getpass

    return getpass.getuser()


def _linger_already_on(runner: CommandRunner) -> bool:
    """Whether linger is already enabled for this user (idempotency guard)."""
    try:
        result = runner(
            ["loginctl", "show-user", _current_user(), "--property=Linger"],
            check=False,
        )
    except (subprocess.CalledProcessError, OSError):
        return False
    return "Linger=yes" in (getattr(result, "stdout", "") or "")

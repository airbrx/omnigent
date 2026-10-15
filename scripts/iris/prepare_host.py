"""Prepare a non-secret launchd configuration; never restarts a running host.

The only change this makes to a host's launchd job is adding
``OMNIGENT_IRIS_CONFIG`` — a *path* to the binding file. No PAT, no tenant id
and no model token is written here; the runtime resolves ``pat_ref`` from the
host's own secret store per invocation.

Repointing the launcher is deliberately opt-in (``--launcher``). The installed
service runs with ``--auto-upgrade``, which installs the coordinator's build
and re-execs; pinning it to a development checkout's venv would freeze the host
on that checkout and silently disable that. Pass ``--launcher`` only when you
mean to take a host off auto-upgrade.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
from pathlib import Path

# Both shapes seen in the field. The console-script form is what the Airbrx
# runbook installed originally; the module form is what `omnigent host install`
# writes now, and is the one that carries --auto-upgrade.
_LAUNCHER_NAMES = {"omni", "omnigent"}
_SERVICE_MODULE = "omnigent.host.service_entry"


def _launcher_index(argv: list[str]) -> int | None:
    """Return the index of the executable to replace, or None if it is a module run.

    A module run (``python -m omnigent.host.service_entry``) has no standalone
    launcher to swap, so repointing it is refused rather than guessed at.
    """
    if _SERVICE_MODULE in argv:
        return None
    if "host" in argv:
        index = argv.index("host") - 1
        if index >= 0 and Path(argv[index]).name in _LAUNCHER_NAMES:
            return index
    raise SystemExit("Unrecognized host launcher; inspect it manually")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--plist",
        type=Path,
        default=Path.home() / "Library/LaunchAgents/ai.omnigent.host.plist",
    )
    parser.add_argument(
        "--launcher",
        action="store_true",
        help="Also repoint the launcher at this checkout's venv (disables auto-upgrade)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    args = parser.parse_args()
    config = args.config.resolve(strict=True)
    os.environ["OMNIGENT_IRIS_CONFIG"] = str(config)
    from omnigent.airbrx.iris.config import bindings
    from omnigent.airbrx.iris.package import bundle_root
    from omnigent.onboarding.provider_config import resolve_secret
    from omnigent.spec.parser import parse
    from omnigent.tools.manager import ToolManager

    rows = bindings()
    if not rows:
        raise SystemExit("No Iris tenant bindings configured")
    for row in rows:
        if not Path(row.workspace).is_dir():
            raise SystemExit("An Iris execution workspace is missing")
        if not row.fixture:
            resolve_secret(row.pat_ref)
    assert len(ToolManager(parse(bundle_root())).get_tool_names()) == 4
    plist = plistlib.loads(args.plist.read_bytes())
    argv = plist["ProgramArguments"]
    index = _launcher_index(argv)
    relaunched = False
    if args.launcher:
        if index is None:
            raise SystemExit(
                "This host runs the service module, not a launcher script; "
                "--launcher cannot repoint it. Reinstall the service from the "
                "intended build instead."
            )
        argv[index] = str(Path(__file__).resolve().parents[2] / ".venv/bin/omnigent")
        relaunched = True
    env = plist.setdefault("EnvironmentVariables", {})
    env["OMNIGENT_IRIS_CONFIG"] = str(config)
    # The host explicitly allowlists this reference, not the PAT.
    #
    # The Iris tool subprocess runs on the host's own interpreter, so the `iris`
    # extra's pinned SDK/MCP versions have to survive an unattended re-install.
    # A --auto-upgrade host pipes the server's install.sh with no arguments; this
    # is what keeps the extra attached to that call (install_oss.sh reads it).
    env.setdefault("OMNIGENT_INSTALL_EXTRAS", "iris")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb" if args.force else "xb") as handle:
        handle.write(plistlib.dumps(plist))
    os.chmod(args.output, 0o600)
    print(
        json.dumps(
            {
                "prepared_plist": str(args.output.resolve()),
                "source_plist": str(args.plist.resolve()),
                "label": plist.get("Label"),
                "tenant_bindings": len(rows),
                "launcher_repointed": relaunched,
                "secret_values_written": False,
                "service_restarted": False,
            }
        )
    )


if __name__ == "__main__":
    main()

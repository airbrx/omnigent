"""Prepare a non-secret launchd configuration; never restarts a running host."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--plist",
        type=Path,
        default=Path.home() / "Library/LaunchAgents/ai.airbrx.omnigent.host.plist",
    )
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
    index = argv.index("host") - 1
    if Path(argv[index]).name not in {"omni", "omnigent"}:
        raise SystemExit("Unrecognized host launcher; inspect it manually")
    argv[index] = str(Path(__file__).resolve().parents[2] / ".venv/bin/omnigent")
    env = plist.setdefault("EnvironmentVariables", {})
    env["OMNIGENT_IRIS_CONFIG"] = str(config)
    # The host explicitly allowlists this reference, not the PAT.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(plistlib.dumps(plist))
    os.chmod(args.output, 0o600)
    print(
        json.dumps(
            {
                "prepared_plist": str(args.output.resolve()),
                "tenant_bindings": len(rows),
                "secret_values_written": False,
                "service_restarted": False,
            }
        )
    )


if __name__ == "__main__":
    main()

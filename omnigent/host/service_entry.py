"""Process entry point used by launchd and systemd host services."""

from __future__ import annotations

import argparse

import click

from omnigent.host import HOST_FATAL_EXIT_CODE


def main() -> int:
    """Run the foreground host command and normalize permanent failures."""
    parser = argparse.ArgumentParser(description="Omnigent host service")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--server", help="Remote Omnigent server URL.")
    mode.add_argument("--local", action="store_true", help="Run a local Omnigent server.")
    # airbrx: the supervised invocation must be able to express the same
    # runtime as a hand-run `omnigent host` — otherwise a shared/always-on or
    # self-upgrading host cannot be installed as a service at all.
    parser.add_argument(
        "--auto-upgrade", action="store_true", help="Keep the host in sync with the server build."
    )
    parser.add_argument(
        "--shared", action="store_true", help="Open this host to any authenticated user."
    )
    parser.add_argument("--workroot", default=None, help="Jail dir for a shared host's guests.")
    args = parser.parse_args()

    from omnigent.cli import cli

    server = "" if args.local else args.server
    host_args = ["host", "--server", server, "--non-interactive"]
    if args.auto_upgrade:
        host_args.append("--auto-upgrade")
    if args.shared:
        host_args.append("--shared")
        if args.workroot:
            host_args.extend(["--workroot", args.workroot])
    try:
        cli.main(
            args=host_args,
            prog_name="omnigent",
            standalone_mode=False,
        )
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        click.echo("Aborted!", err=True)
        return 1
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        # A permanent auth/config failure should leave the service enabled but
        # stopped instead of entering a supervisor restart loop.
        return 0 if code == HOST_FATAL_EXIT_CODE else code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""`python -m omnigent.airbrx.iris.viewer`: start the viewer on loopback.

scripts/iris/dev.sh is the one command; it starts this with a clean
environment. Dev sign-in refuses any bind but loopback before anything listens.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from omnigent.airbrx.iris import runs
from omnigent.airbrx.iris.package import HERE
from omnigent.airbrx.iris.viewer.identity import DEFAULT_DEV_EMAIL, check_dev_bind

FIXTURE_ROOT = HERE / "fixtures" / "runs"
DEFAULT_PORT = 6790


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m omnigent.airbrx.iris.viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("IRIS_VIEWER_PORT") or DEFAULT_PORT)
    )
    parser.add_argument(
        "--auth", choices=["dev"], default=os.environ.get("IRIS_VIEWER_AUTH") or "dev"
    )
    parser.add_argument(
        "--runs",
        action="append",
        default=[],
        metavar="ROOT",
        help="a run root; repeatable (default: the packaged synthetic fixture)",
    )
    parser.add_argument(
        "--reload", action="store_true", default=os.environ.get("IRIS_VIEWER_RELOAD") == "1"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse(argv)
    check_dev_bind(args.host, public_url=os.environ.get("IRIS_VIEWER_PUBLIC_URL"))
    email = os.environ.get("IRIS_VIEWER_DEV_EMAIL") or DEFAULT_DEV_EMAIL
    roots = [Path(os.path.expanduser(r)) for r in args.runs] or [FIXTURE_ROOT]
    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        raise SystemExit(f"Run root not found: {', '.join(missing)}")

    from omnigent.airbrx.iris.viewer.app import DuplicateTenant, create_app

    try:
        create_app(roots, identity=None)
    except DuplicateTenant as exc:
        raise SystemExit(str(exc)) from None

    host = f"[{args.host}]" if ":" in args.host else args.host
    print(f"Iris viewer: http://{host}:{args.port}/iris", flush=True)
    for root in roots:
        tenants = runs.scan(root)
        every = [r for t in tenants for r in t.runs]
        print(f"  runs: {root} ({len(tenants)} tenants, {len(every)} runs)", flush=True)
        for run in every:
            if not run.readable:
                print(f"    unreadable: {run.tenant_id}/{run.week}: {run.problem}", flush=True)
    print(f"Dev sign-in: {email}", flush=True)
    print("Agent: not connected (viewer)", flush=True)

    import uvicorn

    os.environ["IRIS_VIEWER__ROOTS"] = os.pathsep.join(str(r) for r in roots)
    os.environ["IRIS_VIEWER__EMAIL"] = email
    uvicorn.run(
        "omnigent.airbrx.iris.viewer.app:app_from_env",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(HERE)] if args.reload else None,
        log_level="info",
    )


if __name__ == "__main__":
    sys.exit(main())

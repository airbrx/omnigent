"""Snapshot only reviewed, tracked Iris package/bundle/public UI files."""

import argparse
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

target = Path(__file__).resolve().parents[2] / "omnigent/airbrx/iris"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument(
    "--revision",
    default=json.loads((target / "source.json").read_text())["revision"],
    help="Reviewed Iris revision; defaults to the currently pinned revision",
)
args = parser.parse_args()
source = args.source.resolve()
try:
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "--verify", f"{args.revision}^{{commit}}"],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
except subprocess.CalledProcessError:
    # Say what is actually wrong. The default for --revision is whatever
    # source.json already pins, and a pinned revision can be rebased or
    # squashed out of existence after the archive is cut — at which point this
    # died in a subprocess traceback reading "exit status 128", and working out
    # that the commit simply was not in the repository took hashing all 44
    # archive members against every commit in it.
    raise SystemExit(
        f"{args.revision!r} is not a commit in {source}.\n"
        f"If that is the revision currently pinned in source.json, it is no longer reachable "
        f"in this repository — rebased or squashed away after the archive was built. Re-pin "
        f"against a reviewed revision explicitly:\n"
        f"    python {Path(__file__).name} {source} --revision <reviewed sha or branch>"
    ) from None
files = subprocess.check_output(
    ["git", "-C", str(source), "ls-tree", "-r", "--name-only", revision], text=True
).splitlines()
# The app, and the evidence a session produces for itself. No captures.
#
# `ui/iris-state.json` was never vendored, because it is someone's captured
# tenant evidence. `ui/demo-state.json` is out for a subtler reason that turns
# out to be the same one: app.js boots through a fallback chain — live host
# evidence, then a captured report, then this synthetic demo, then nothing — so
# shipping it meant a fresh, authenticated, tenant-bound session could open on a
# complete fabricated cache report behind a small "Synthetic demo" chip, and
# `ask()` then answered questions from it without ever calling the host.
#
# The hosted route already refuses to serve it. This removes the rung itself, so
# the next person to write a serving path does not inherit the trap. The file
# stays in the repository — the local development bridge serves `ui/` straight
# from a checkout and its demo is useful there. It just does not travel.
allowed = [
    p
    for p in files
    if p.startswith(("iris/", "omnigent/", "ui/assets/"))
    or p
    in {
        "ui/index.html",
        "ui/app.js",
        "ui/style.css",
        "ui/theme.js",
        "pyproject.toml",
    }
]
stream = io.BytesIO()
with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in sorted(allowed):
        data = subprocess.check_output(["git", "-C", str(source), "show", f"{revision}:{name}"])
        info = zipfile.ZipInfo(name, (2026, 9, 14, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, data)
blob = stream.getvalue()
(target / "iris-source.zip").write_bytes(blob)
(target / "source.json").write_text(
    json.dumps(
        {
            "repository": "airbrx/iris",
            "revision": revision,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "files": allowed,
        },
        indent=2,
    )
    + "\n"
)
print(revision, len(blob), "bytes; private capture excluded")

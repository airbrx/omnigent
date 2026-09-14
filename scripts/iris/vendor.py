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
revision = subprocess.check_output(
    ["git", "-C", str(source), "rev-parse", "--verify", f"{args.revision}^{{commit}}"], text=True
).strip()
files = subprocess.check_output(
    ["git", "-C", str(source), "ls-tree", "-r", "--name-only", revision], text=True
).splitlines()
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
        "ui/demo-state.json",
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

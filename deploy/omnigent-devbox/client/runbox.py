#!/usr/bin/env python3
"""Run a local shell script on the dev box via SSM and print sanitized output.

Exists because hand-writing SSM --parameters JSON in bash kept breaking on
backslash escapes, and because the AWS CLI cannot print non-ASCII to this
Windows console (cp1252). Both problems vanish if Python builds the JSON and
decodes the result.

    python runbox.py <script.sh> [instance-id]
"""

import base64
import json
import os
import pathlib
import subprocess
import sys

INSTANCE = "i-099d66548b496d876"

# The AWS CLI is a frozen Python app; on this Windows box it writes to its own
# stdout with cp1252 and dies on any non-ASCII byte in the response (systemd's
# check marks, arrows, etc.). Forcing UTF-8 into its environment is the fix.
_AWS_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def aws(*args: str) -> str:
    """Run an aws command, returning stdout. Fails loudly."""
    proc = subprocess.run(
        ["aws", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_AWS_ENV,
    )
    if proc.returncode != 0:
        sys.exit(f"aws {' '.join(args)} failed:\n{proc.stderr}")
    return proc.stdout


def main() -> None:
    script = pathlib.Path(sys.argv[1])
    instance = sys.argv[2] if len(sys.argv) > 2 else INSTANCE
    b64 = base64.b64encode(script.read_bytes()).decode()
    remote = f"/tmp/{script.name}"
    params = {
        "commands": [
            f"echo {b64} | base64 -d > {remote}",
            f"chmod 755 {remote}",
            f"bash {remote}",
            f"rm -f {remote}",
        ]
    }
    payload = pathlib.Path("_params.json")
    payload.write_text(json.dumps(params), encoding="utf-8")

    out = aws(
        "ssm",
        "send-command",
        "--instance-ids",
        instance,
        "--document-name",
        "AWS-RunShellScript",
        "--parameters",
        f"file://{payload}",
        "--output",
        "json",
    )
    cid = json.loads(out)["Command"]["CommandId"]
    print(f"CommandId={cid}", flush=True)

    subprocess.run(
        ["aws", "ssm", "wait", "command-executed", "--command-id", cid, "--instance-id", instance],
        capture_output=True,
        env=_AWS_ENV,
    )
    inv = json.loads(
        aws(
            "ssm",
            "get-command-invocation",
            "--command-id",
            cid,
            "--instance-id",
            instance,
            "--output",
            "json",
        )
    )
    print("STATUS:", inv.get("Status"))
    body = inv.get("StandardOutputContent") or ""
    err = inv.get("StandardErrorContent") or ""
    if err.strip():
        body += "\n--- STDERR ---\n" + err
    # cp1252 console: replace anything it cannot render rather than crashing.
    sys.stdout.write(body.encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    main()

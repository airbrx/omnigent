"""Run Iris's four existing tools in the native runner and attach owned reports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import httpx

from omnigent.airbrx.iris.config import session_binding
from omnigent.airbrx.iris.package import bundle_root, source_root, validate_spec

TOOLS = frozenset({"iris_overview", "iris_investigate", "iris_audit", "iris_propose"})


async def invoke(tool_name, arguments, *, spec, session_id, client):
    validate_spec(spec)
    if tool_name not in TOOLS or client is None or not session_id:
        raise ValueError("Iris requires an authenticated native session and an allowed tool")
    response = await client.get(f"/v1/sessions/{session_id}")
    response.raise_for_status()
    session = response.json()
    if session.get("agent_name") != "iris":
        raise ValueError("Iris requires the registered agent session")
    from omnigent.debug_logging import USER_ID_ENV_VAR

    owner = os.environ.get(USER_ID_ENV_VAR)
    if not owner:
        raise ValueError("Iris requires the execution host's authenticated owner identity")
    binding = session_binding(session, owner)
    workspace = Path(binding.workspace).resolve(strict=True)
    root = workspace / ".iris" / hashlib.sha256(session_id.encode()).hexdigest()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("Iris state cannot use symlinks")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    # Pin the tenant before any execution; changing operator config requires a new session.
    identity = root / "tenant.json"
    selected_identity = {"tenant_id": binding.tenant_id, "fixture": binding.fixture}
    if identity.exists() and json.loads(identity.read_text()) != selected_identity:
        raise ValueError("Iris tenant changed; start a new session")
    identity.write_text(json.dumps(selected_identity))
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"HOME", "PATH", "LANG", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    }
    env.update(
        {
            "PYTHONPATH": str(source_root()),
            "_AP_RESPONSE_MODE": "stdout",
            "IRIS_TENANT_ID": binding.tenant_id,
            "IRIS_FIXTURE": str(int(binding.fixture)),
            "IRIS_OUTPUT_DIR": str(root / "reports"),
        }
    )
    if not binding.fixture:
        from omnigent.onboarding.provider_config import resolve_secret

        env["AIRBRX_PAT"] = resolve_secret(binding.pat_ref)
    from omnigent.tools import local

    payload = json.dumps(
        {
            "module_path": str(bundle_root() / f"tools/python/{tool_name}.py"),
            "tool_name": tool_name,
            "arguments": json.loads(arguments),
            "state_root": str(root / "state"),
        }
    ).encode()
    proc = await _spawn_tool(
        sys.executable,
        str(Path(local.__file__).with_name("_runner.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=workspace,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(payload), timeout=300)
    except (asyncio.CancelledError, TimeoutError):
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    assert proc.returncode is not None
    raw = local._read_stdout_response(stdout, proc.returncode, stderr)
    try:
        result = json.loads(raw)
    except ValueError:
        # Never reflect subprocess diagnostics, which may contain credential material.
        return json.dumps(
            {"error": "Iris tool failed; check host configuration", "incomplete": True}
        )
    if not isinstance(result, dict):
        raise ValueError("Invalid Iris result")
    artifacts = result.pop("artifacts", {})
    if artifacts:
        result["downloads"] = await deliver(
            artifacts, session_id, binding.tenant_id, root / "reports", client
        )
    return json.dumps(result)


async def deliver(
    artifacts: dict, session_id: str, tenant: str, output: Path, client: httpx.AsyncClient
) -> list[dict]:
    allowed_root = output.resolve() / hashlib.sha256(tenant.encode()).hexdigest()
    downloads = []
    for name, value in artifacts.items():
        path = Path(value)
        if (
            name not in {"report.json", "report.md", "proposal.json"}
            or path.name != name
            or any(p.is_symlink() for p in (path, *path.parents))
            or not path.resolve().is_relative_to(allowed_root)
        ):
            raise ValueError("Iris artifact is outside its session and tenant")
        manifest = json.loads((path.parent / "manifest.json").read_text())
        if (
            manifest.get("owner") != "iris-v1"
            or manifest.get("tenantId") != tenant
            or name not in manifest.get("files", [])
        ):
            raise ValueError("Iris artifact manifest does not authorize delivery")
        data = path.read_bytes()
        response = await client.post(
            f"/v1/sessions/{session_id}/resources/files", files={"file": (name, data)}, timeout=60
        )
        response.raise_for_status()
        file_id = response.json()["id"]
        downloads.append(
            {
                "file_id": file_id,
                "filename": name,
                "url": f"/v1/sessions/{session_id}/resources/files/{file_id}/content",
            }
        )
    return downloads


async def _spawn_tool(*args, **kwargs):
    return await asyncio.create_subprocess_exec(*args, **kwargs)

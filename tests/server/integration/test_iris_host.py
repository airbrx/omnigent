"""Real Iris subprocesses and real session artifact routes, with synthetic telemetry."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from omnigent.airbrx.iris.package import bundle_root, validate_spec
from omnigent.airbrx.iris.runtime import TOOLS, deliver, invoke
from omnigent.cli import _preregister_agent
from omnigent.runtime import (
    get_agent_cache,
    get_agent_store,
    get_artifact_store,
    get_conversation_store,
)
from omnigent.spec.parser import parse
from omnigent.tools.manager import ToolManager


@pytest.fixture
def iris_session(client, tmp_path, monkeypatch):
    root = tmp_path.resolve()
    spec = parse(bundle_root())
    agent_id = _preregister_agent(
        bundle_root(), get_agent_store(), get_artifact_store(), get_agent_cache()
    )
    session = get_conversation_store().create_conversation(
        agent_id=agent_id, host_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", workspace=str(root)
    )
    config = root / "binding.json"
    config.write_text(
        json.dumps(
            [
                {
                    "tenant_id": "fixture-ci",
                    "host_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "workspace": str(root),
                    "users": ["fixture-owner"],
                    "fixture": True,
                    "pat_ref": "",
                }
            ]
        )
    )
    monkeypatch.setenv("OMNIGENT_IRIS_CONFIG", str(config))
    monkeypatch.setenv("OMNIGENT_USER_ID", "fixture-owner")
    return spec, session, root, config


async def test_real_tool_subprocess_and_session_owned_downloads(client, iris_session):
    spec, session, root, _ = iris_session
    assert set(ToolManager(spec).get_tool_names()) == TOOLS
    from omnigent.runner.app import _spec_with_workdir_paths
    from omnigent.runner.tool_dispatch import execute_tool

    result = json.loads(
        await execute_tool(
            tool_name="iris_overview",
            arguments="{}",
            agent_spec=_spec_with_workdir_paths(spec, bundle_root()),
            conversation_id=session.id,
            server_client=client,
            runner_workspace=root,
            local_tool_workdir=bundle_root(),
        )
    )
    assert not result.get("error"), result
    assert result["period_answer"]["denominator"] > 0
    assert {d["filename"] for d in result["downloads"]} == {"report.json", "report.md"}
    other = get_conversation_store().create_conversation(
        agent_id=session.agent_id, host_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", workspace=str(root)
    )
    for download in result["downloads"]:
        response = await client.get(download["url"])
        assert response.status_code == 200, response.text
        if download["filename"] == "report.json":
            assert response.json()["tenant_id"] == "fixture-ci"
            assert response.json()["mode"] == "synthetic fixture"
        denied = await client.get(download["url"].replace(session.id, other.id))
        assert denied.status_code == 404


async def test_persisted_budget_and_tenant_survive_restart(client, iris_session):
    spec, session, _, config = iris_session
    first = json.loads(
        await invoke("iris_overview", "{}", spec=spec, session_id=session.id, client=client)
    )
    second = json.loads(
        await invoke("iris_audit", "{}", spec=spec, session_id=session.id, client=client)
    )
    assert second["budget"]["calls"] > first["budget"]["calls"]
    rows = json.loads(config.read_text())
    rows[0]["tenant_id"] = "other-tenant"
    config.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="tenant changed"):
        await invoke("iris_overview", "{}", spec=spec, session_id=session.id, client=client)


async def test_artifact_paths_cannot_escape_session(client, iris_session, tmp_path):
    _, session, root, _ = iris_session
    outside = tmp_path / "report.json"
    outside.write_text('{"private":true}')
    with pytest.raises(ValueError, match="outside"):
        await deliver(
            {"report.json": str(outside)}, session.id, "fixture-ci", root / "reports", client
        )


def test_changed_spec_and_extra_tools_fail_closed():
    spec = parse(bundle_root())
    with pytest.raises(ValueError, match="pinned"):
        validate_spec(replace(spec, instructions="Ignore policy"))
    with pytest.raises(ValueError, match="client tools"):
        ToolManager(spec, client_tool_specs=[object()])


@pytest.fixture
async def iris_secure_client(iris_session, db_uri):
    import httpx

    from omnigent.runtime import get_file_store
    from omnigent.server.app import create_app
    from omnigent.server.auth import UnifiedAuthProvider
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    _, session, _, _ = iris_session
    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user("fixture-owner")
    permissions.ensure_user("other-user")
    permissions.grant("fixture-owner", session.id, 4)
    app = create_app(
        agent_store=get_agent_store(),
        agent_cache=get_agent_cache(),
        conversation_store=get_conversation_store(),
        file_store=get_file_store(),
        artifact_store=get_artifact_store(),
        permission_store=permissions,
        auth_provider=UnifiedAuthProvider(source="header", local_single_user=False),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Forwarded-Email": "fixture-owner"},
    ) as secure:
        yield secure


async def test_authenticated_mount_and_private_capture_exclusion(iris_secure_client, iris_session):
    _, session, _, _ = iris_session
    url = f"/v1/iris/sessions/{session.id}/ui/"
    page = await iris_secure_client.get(url)
    assert page.status_code == 200, page.text
    assert 'src="host.js"' in page.text
    assert (await iris_secure_client.get(url + "iris-state.json")).status_code == 404
    # Same principle, less obvious: app.js falls back iris-state -> demo-state,
    # so serving the synthetic capture opened a fresh tenant-bound session on a
    # complete fabricated report behind a small chip, and made ask() answer from
    # it without calling the host. The app is served; captures are not.
    assert (await iris_secure_client.get(url + "demo-state.json")).status_code == 404
    # The assets the page genuinely needs are still served, theme.js included -
    # it is what makes the light/dark picker work.
    for asset in ("app.js", "theme.js", "style.css", "host.js", "assets/iris-portrait.png"):
        assert (await iris_secure_client.get(url + asset)).status_code == 200, asset
    assert (await iris_secure_client.get(url + "api/state")).status_code == 409
    iris_secure_client.headers["X-Forwarded-Email"] = "other-user"
    assert (await iris_secure_client.get(url)).status_code == 404
    iris_secure_client.headers.pop("X-Forwarded-Email")
    assert (await iris_secure_client.get(url)).status_code == 401


async def test_report_state_requires_native_tool_provenance(iris_secure_client, iris_session):
    from omnigent.entities import FunctionCallData, FunctionCallOutputData, NewConversationItem

    spec, session, _, _ = iris_session
    result = await invoke(
        "iris_overview", "{}", spec=spec, session_id=session.id, client=iris_secure_client
    )
    state_url = f"/v1/iris/sessions/{session.id}/ui/api/state"
    # Files alone do not prove an Iris tool completed.
    assert (await iris_secure_client.get(state_url)).status_code == 409
    get_conversation_store().append(
        session.id,
        [
            NewConversationItem(
                type="function_call",
                response_id="a" * 32,
                data=FunctionCallData(
                    agent="iris", name="iris_overview", arguments="{}", call_id="call-iris"
                ),
            ),
            NewConversationItem(
                type="function_call_output",
                response_id="a" * 32,
                data=FunctionCallOutputData(call_id="call-iris", output=result),
            ),
        ],
    )
    response = await iris_secure_client.get(state_url)
    assert response.status_code == 200, response.text
    assert response.json()["overview"]["tenant_id"] == "fixture-ci"
    assert response.json()["monitoring"] is None
    download = json.loads(result)["downloads"][0]["url"]
    iris_secure_client.headers["X-Forwarded-Email"] = "other-user"
    assert (await iris_secure_client.get(download)).status_code == 404


async def test_unknown_workspace_and_tenant_are_denied(iris_secure_client, iris_session):
    from omnigent.db.db_models import workspace_scope

    _, session, _, config = iris_session
    url = f"/v1/iris/sessions/{session.id}/ui/"
    with workspace_scope(2):
        assert (await iris_secure_client.get(url)).status_code == 404
    rows = json.loads(config.read_text())
    rows[0]["users"] = ["another-owner"]
    config.write_text(json.dumps(rows))
    assert (await iris_secure_client.get(url)).status_code == 403


def test_answer_contract_rejects_unreviewed_or_extra_tool_output():
    from fastapi import HTTPException

    from omnigent.airbrx.iris.routes import completed_answer, report_references

    message = {
        "type": "message",
        "role": "assistant",
        "status": "in_progress",
        "content": [{"type": "output_text", "text": "Not yet reviewed"}],
    }
    assert completed_answer([message]) is None
    message["status"] = "completed"
    assert completed_answer([message])["text"] == "Not yet reviewed"
    with pytest.raises(HTTPException):
        completed_answer([message, {"type": "function_call", "name": "sys_scheduled_task_create"}])
    assert (
        report_references(
            [
                {
                    "type": "function_call_output",
                    "call_id": "fake",
                    "output": '{"downloads":[{"filename":"report.json","file_id":"fake"}]}',
                }
            ]
        )
        == []
    )


async def test_budget_exhaustion_is_an_honest_tool_error(client, iris_session):
    import hashlib

    spec, session, root, _ = iris_session
    await invoke("iris_overview", "{}", spec=spec, session_id=session.id, client=client)
    budget = (
        root / ".iris" / hashlib.sha256(session.id.encode()).hexdigest() / "state/iris-budget.json"
    )
    state = json.loads(budget.read_text())
    state["calls"] = 60
    budget.write_text(json.dumps(state))
    result = json.loads(
        await invoke("iris_overview", "{}", spec=spec, session_id=session.id, client=client)
    )
    assert result["error_code"] == "budget"
    assert result["incomplete"] is True


async def test_cancellation_kills_tool_and_scopes_credential_handoff(
    client, iris_session, monkeypatch
):
    import asyncio
    import sys

    from omnigent.airbrx.iris import runtime

    spec, session, _, config = iris_session
    rows = json.loads(config.read_text())
    rows[0].update(fixture=False, pat_ref="keychain:iris-fixture-test")
    config.write_text(json.dumps(rows))
    monkeypatch.setattr(
        "omnigent.onboarding.provider_config.resolve_secret", lambda ref: "fixture-secret"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-domain-tool")
    spawned = asyncio.Event()
    process = None

    async def start(*args, **kwargs):
        nonlocal process
        assert kwargs["env"]["AIRBRX_PAT"] == "fixture-secret"
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert "RUNNER_INITIAL_AUTH_TOKEN" not in kwargs["env"]
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        spawned.set()
        return process

    monkeypatch.setattr(runtime, "_spawn_tool", start)
    task = asyncio.create_task(
        invoke("iris_overview", "{}", spec=spec, session_id=session.id, client=client)
    )
    await asyncio.wait_for(spawned.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode is not None


async def test_native_dispatch_refuses_ambient_builtin_before_side_effect(client, iris_session):
    from omnigent.runner.tool_dispatch import execute_tool

    spec, session, root, _ = iris_session
    result = json.loads(
        await execute_tool(
            tool_name="sys_scheduled_task_create",
            arguments="{}",
            agent_spec=spec,
            conversation_id=session.id,
            server_client=client,
            runner_workspace=root,
        )
    )
    assert result == {"error": "Iris permits only its four read-only domain tools"}


def test_runner_forwards_only_binding_reference(monkeypatch):
    from omnigent.host.connect import _build_runner_env

    env = _build_runner_env(
        {
            "HOME": "/fixture",
            "OMNIGENT_IRIS_CONFIG": "/fixture/binding.json",
            "AIRBRX_PAT": "fixture-secret",
        },
        server_url="https://fixture.invalid",
        runner_id="r",
        binding_token="b",
        workspace="/fixture",
        parent_pid=1,
    )
    assert env["OMNIGENT_IRIS_CONFIG"] == "/fixture/binding.json"
    assert "AIRBRX_PAT" not in env


@pytest.fixture
async def iris_protocol_client(iris_session, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from omnigent.airbrx.iris import routes
    from omnigent.server.auth import UnifiedAuthProvider

    _, session, root, _ = iris_session
    recorded = []
    mode = {"status": "idle", "answer": True, "task_error": None}

    def native(request):
        if request.method == "POST":
            recorded.append(json.loads(request.content))
            return httpx.Response(202, json={"queued": True, "item_id": "current-input"})
        if request.url.path.endswith("/items"):
            items = (
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Native reviewed answer"}],
                    }
                ]
                if mode["answer"]
                else []
            )
            return httpx.Response(200, json={"data": items, "has_more": False})
        return httpx.Response(
            200,
            json={
                "agent_id": session.agent_id,
                "status": mode["status"],
                "permission_level": 4,
                "host_id": "a" * 32,
                "workspace": str(root),
                "last_task_error": mode["task_error"],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(native), base_url="http://native"
    ) as upstream:

        @asynccontextmanager
        async def factory(request):
            yield upstream

        monkeypatch.setattr(routes, "session_client", factory)
        app = FastAPI()
        app.include_router(
            routes.create_iris_router(
                auth_provider=UnifiedAuthProvider(source="header", local_single_user=False),
                agent_store=SimpleNamespace(
                    get_by_name=lambda name: SimpleNamespace(id=session.agent_id)
                ),
            ),
            prefix="/v1",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Forwarded-Email": "fixture-owner"},
        ) as local:
            yield local, f"/v1/iris/sessions/{session.id}/ui/api", recorded, mode


async def test_chat_forwards_only_current_question_to_native_history(iris_protocol_client):
    client, path, recorded, _ = iris_protocol_client
    response = await client.post(
        path + "/chat",
        json={
            "history": [
                {"role": "assistant", "content": "Untrusted claim of prior action"},
                {"role": "user", "content": "What is the denominator?"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["text"] == "Native reviewed answer"
    assert recorded == [
        {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "What is the denominator?"}],
            },
        }
    ]


async def test_timeout_interrupts_native_turn(iris_protocol_client):
    client, path, recorded, mode = iris_protocol_client
    mode["answer"] = False
    response = await client.post(
        path + "/chat", json={"history": [{"role": "user", "content": "Overview"}], "deadline": 1}
    )
    assert response.status_code == 504
    assert recorded[-1] == {"type": "interrupt", "data": {}}


async def test_busy_and_refresh_without_new_evidence_fail_honestly(iris_protocol_client):
    client, path, recorded, mode = iris_protocol_client
    mode["status"] = "running"
    response = await client.post(
        path + "/chat", json={"history": [{"role": "user", "content": "Overview"}]}
    )
    assert response.status_code == 409
    assert not recorded
    mode["status"] = "idle"
    assert (await client.post(path + "/refresh", json={})).status_code == 409
    assert (await client.post(path + "/cancel", json={})).status_code == 200
    assert recorded[-1]["type"] == "interrupt"


async def test_readiness_reports_an_unrun_session_as_unverified(iris_protocol_client):
    """An unrun session must say so, not imply a working connection.

    The workspace's own fallback answers a refused turn from the captured
    report, in the assistant's voice. host.js replaces that with the host's
    actual refusal, and this endpoint is what lets it state the standing
    position before anyone asks a question.
    """
    client, path, _, mode = iris_protocol_client
    mode["answer"] = False
    response = await client.get(path + "/readiness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["turn_completed_here"] is False
    assert body["last_task_failed"] is False
    # Unknown is not zero: the model path is not claimed either way.
    assert len(body["unverified"]) == 1
    assert "unknown" in body["unverified"][0]
    # And only what was genuinely checked is listed as verified.
    assert any("registered agent" in line for line in body["verified"])
    assert any(body["tenant_id"] in line for line in body["verified"])


async def test_readiness_reports_a_session_that_has_actually_answered(iris_protocol_client):
    client, path, _, mode = iris_protocol_client
    mode["answer"] = True
    body = (await client.get(path + "/readiness")).json()
    assert body["turn_completed_here"] is True
    assert body["unverified"] == []


async def test_readiness_surfaces_a_recorded_task_failure_without_echoing_it(
    iris_protocol_client,
):
    """A failure is reported; the host's error text is not forwarded.

    The rest of this module refuses to reflect execution diagnostics because
    they can carry credential material, and a readiness banner is no place to
    make an exception.
    """
    client, path, _, mode = iris_protocol_client
    mode["task_error"] = "boom: AIRBRX_PAT=super-secret rejected"
    body = (await client.get(path + "/readiness")).json()
    assert body["last_task_failed"] is True
    assert "super-secret" not in json.dumps(body)


async def test_readiness_refuses_a_session_that_is_not_the_caller_s_iris(iris_secure_client):
    """Readiness is behind the same authorization as every other hosted route."""
    client = iris_secure_client
    assert (
        await client.get("/v1/iris/sessions/not-a-session/ui/api/readiness")
    ).status_code >= 400


async def test_portrait_comes_from_the_pinned_package_and_needs_authentication(
    iris_secure_client,
):
    """The drawer's picture of Iris is the one her verified package ships.

    Not a copy committed into the web assets, which would drift from the
    package silently, and not an open endpoint: it goes out under the same
    authentication as everything else in this module.
    """
    response = await iris_secure_client.get("/v1/iris/portrait")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    anonymous = await iris_secure_client.get(
        "/v1/iris/portrait", headers={"X-Forwarded-Email": ""}
    )
    assert anonymous.status_code == 401

"""Turn a Tally binding into the environment her MCP client expands.

The counterpart of ``omnigent/airbrx/eva/runtime.py``, and deliberately the same
two functions with the same split. Read that module for the reasoning; the
short form:

Tally's bundle declares one remote MCP server:

    url: ${TALLY_PORTAL_MCP_URL}
    headers:
      Authorization: Bearer ${TALLY_PORTAL_MCP_TOKEN}

Those references expand on the runner, against the runner's own process
environment (``env_expansion: runner``). :func:`launch_env` is registered with
:mod:`omnigent.runtime.launch_env` at server start, and every host runner launch
spreads its answer into the launch frame: the URL as a value and the token as a
*reference*. The host resolves the reference from its own store
(``omnigent/host/connect.py`` ``resolve_agent_secret_env``), so the coordinator
routes never hold the token.

Where a hostless binding leaves this is written up in docs/tally/RUNBOOK.md
("Where Tally runs").
"""

from __future__ import annotations

from omnigent.airbrx.tally.config import Binding, bindings
from omnigent.runtime.launch_env import LaunchEnv

#: The bundle's ``url:``. ``tests/airbrx/test_tally_bundle.py`` reads the bundle
#: and these names have to agree with it.
PORTAL_URL_VAR = "TALLY_PORTAL_MCP_URL"

#: The bundle's ``Authorization: Bearer`` header.
PORTAL_TOKEN_VAR = "TALLY_PORTAL_MCP_TOKEN"

_NO_TOKEN_REF = (
    "A live Tally binding needs a token_ref: without one she would reach the real "
    "portal with no credential, which is the fixture shape wearing a live label"
)


def session_env(binding: Binding) -> dict[str, str]:
    """The environment one session's runner needs to reach Tally's tools.

    :param binding: The binding selected for the caller.
    :returns: :data:`PORTAL_URL_VAR` and, unless the binding is a fixture,
        :data:`PORTAL_TOKEN_VAR`, resolved here.
    :raises ValueError: If a live binding names no ``token_ref``.
    :raises OmnigentError: If ``token_ref`` does not resolve.

    Complete or raising, never partial: a URL without a token connects and
    answers 401, which sends the operator to the wrong fault.
    """
    env = {PORTAL_URL_VAR: binding.mcp_url()}
    if binding.fixture:
        return env
    if not binding.token_ref:
        raise ValueError(_NO_TOKEN_REF)
    # Imported at call time, like Eva's, so this module stays importable in a
    # checkout that cannot open a keychain.
    from omnigent.onboarding.provider_config import resolve_secret

    env[PORTAL_TOKEN_VAR] = resolve_secret(binding.token_ref)
    return env


def launch_env(agent_name: str, user_id: str | None) -> LaunchEnv | None:
    """Tally's provider for :mod:`omnigent.runtime.launch_env`.

    :param agent_name: The agent being launched. Anything but ``"tally"`` is
        not ours and gets ``None``.
    :param user_id: The acting user, matched against each binding's ``users``.
    :returns: The URL as a value and the token as a reference, or ``None`` when
        this user has no single unambiguous binding.

    ``host_id`` plays no part here, exactly as in Eva's provider: a binding
    with or without one yields the same mapping. Whether anything applies it is
    decided by the launch path, not by this function.
    """
    if agent_name != "tally" or user_id is None:
        return None

    candidates = [b for b in bindings() if user_id in b.users]
    if len(candidates) != 1:
        return None
    binding = candidates[0]

    env = {PORTAL_URL_VAR: binding.mcp_url()}
    if binding.fixture:
        return LaunchEnv(env=env)
    if not binding.token_ref:
        raise ValueError(_NO_TOKEN_REF)
    return LaunchEnv(env=env, secret_refs={PORTAL_TOKEN_VAR: binding.token_ref})

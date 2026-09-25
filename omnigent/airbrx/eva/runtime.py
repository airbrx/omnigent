"""Turn an Eva binding into the environment her MCP client expands.

The counterpart of ``omnigent/airbrx/iris/runtime.py``, which resolves Iris's
``pat_ref`` into ``AIRBRX_PAT`` when it builds the environment for a turn. Eva
had no equivalent, and that was the gap between a binding existing and Eva's
tools working.

## Why the gap was invisible

Eva's bundle declares one remote MCP server:

    url: ${OUTREACH_MCP_URL}
    headers:
      Authorization: Bearer ${OUTREACH_MCP_TOKEN}

Those references expand in :mod:`omnigent.runner._entry`, on the runner, against
the runner's own process environment, and only for operator-authored agents
(``X-Agent-Session-Scoped: false``). Nothing put the two values there. So a
bound Eva loaded, listed her tools, and answered 401 on every call, with a
literal ``${OUTREACH_MCP_TOKEN}`` in the header. Every diagnosis of that
treated it as local wiring that the hosted path would avoid, because the
binding carries a ``token_ref`` and it reads as though something resolves it.
Nothing did: across the repository ``token_ref`` appeared only in the allow
list of permitted keys, the dataclass field, and the parser.

## Why an environment mapping is the right shape, and where it goes

``omnigent/host/connect.py`` states at the runner launch it performs: **one
live runner per session**. That is what makes this safe. A runner process
serves exactly one session, so a token layered into its environment belongs to
one user's turn and cannot reach another's. The same file already injects
per-session values there (``PRIMARY_SESSION_ID``, ``USER_ID``), so this
mapping joins values of a kind the launch path already carries.

Two alternatives were considered and rejected, and they are recorded because
both look obviously correct until you check one thing:

*Expanding the bundle server-side, when it is served.* The server knows the
session, therefore the user, therefore the binding, so it could hand the runner
an already-expanded bundle. It must not. :mod:`omnigent.runner._entry` caches
the loaded spec at ``_agent_cache_dest(root, agent_id, version)``, keyed on the
agent id and version and nothing else, so a bundle expanded with one user's
token is reused for the next user of the same agent version.

*Making Eva's tools local Python, the way Iris's are.* It works, it is proven
in production, and it discards the entire MCP surface to solve a wiring
problem.

## What this module deliberately does not do

It does not read the keychain itself, does not log, and does not cache. The
value it returns is a secret with the lifetime of one call: the caller layers
it into one runner's environment and drops it. Nothing here writes a token to
disk, to a bundle, or to a launchd plist, which is the constraint that made
``OMNIGENT_RUNNER_ENV_PASSTHROUGH`` an acceptable bridge for one developer on
one Mac and an unacceptable design for a product where each rep holds a
different token.
"""

from __future__ import annotations

from omnigent.airbrx.eva.config import Binding, bindings
from omnigent.runtime.launch_env import LaunchEnv

#: The bundle's ``url:``. Named here so the bundle and the loader cannot drift
#: apart silently; ``tests/airbrx/test_eva_bundle.py`` reads the bundle and
#: these names have to agree with it.
OUTREACH_URL_VAR = "OUTREACH_MCP_URL"

#: The bundle's ``Authorization: Bearer`` header.
OUTREACH_TOKEN_VAR = "OUTREACH_MCP_TOKEN"


def session_env(binding: Binding) -> dict[str, str]:
    """The environment one session's runner needs to reach this Eva's tools.

    :param binding: The binding selected for the caller, from
        :func:`omnigent.airbrx.eva.config.bindings`.
    :returns: A mapping to layer onto that session's runner environment,
        carrying :data:`OUTREACH_URL_VAR` and, unless the binding is a
        fixture, :data:`OUTREACH_TOKEN_VAR`.
    :raises ValueError: If a live binding names no ``token_ref``.
    :raises OmnigentError: If ``token_ref`` names a secret that is not stored,
        or an environment variable that is not set.

    Complete or raising, never partial. A mapping with the URL and no token
    lets the client connect and the app answer 401, which sends the operator
    to look at a credential they believe is configured. The reference failing
    to resolve is the actual fault and it is reported where it happens.
    """
    env = {OUTREACH_URL_VAR: binding.mcp_url()}

    if binding.fixture:
        # A fixture binding names no secret because there is no real app
        # behind it. The missing token is the condition under test, not a
        # misconfiguration.
        return env

    if not binding.token_ref:
        raise ValueError(
            "A live Eva binding needs a token_ref: without one she would reach a "
            "real outreach app with no credential, which is the fixture shape "
            "wearing a live label"
        )

    # Imported at call time, like Iris does it, so this module stays importable
    # in a checkout that cannot open a keychain.
    from omnigent.onboarding.provider_config import resolve_secret

    env[OUTREACH_TOKEN_VAR] = resolve_secret(binding.token_ref)
    return env


def launch_env(agent_name: str, user_id: str | None) -> LaunchEnv | None:
    """Eva's provider for :mod:`omnigent.runtime.launch_env`.

    The difference from :func:`session_env` is where the token is resolved, and
    it is the whole point. ``session_env`` resolves here, which is right only
    when "here" is the machine holding the secret. This returns the *reference*
    and lets the host resolve it, so the coordinator that builds the launch
    request never holds Eva's bearer token and never can.

    :param agent_name: The agent being launched. Anything but ``"eva"`` is not
        ours and gets ``None``.
    :param user_id: The acting user, matched against each binding's ``users``.
    :returns: The URL as a value and the token as a reference, or ``None`` when
        this user has no single unambiguous binding.

    Ambiguity is declined rather than guessed. Two bindings for one user is the
    same condition ``/v1/eva/readiness`` refuses with a 404: picking one would
    silently send a rep's turn at the wrong app.
    """
    if agent_name != "eva" or user_id is None:
        return None

    candidates = [b for b in bindings() if user_id in b.users]
    if len(candidates) != 1:
        return None
    binding = candidates[0]

    env = {OUTREACH_URL_VAR: binding.mcp_url()}
    if binding.fixture:
        return LaunchEnv(env=env)

    if not binding.token_ref:
        raise ValueError(
            "A live Eva binding needs a token_ref: without one she would reach a "
            "real outreach app with no credential, which is the fixture shape "
            "wearing a live label"
        )
    return LaunchEnv(env=env, secret_refs={OUTREACH_TOKEN_VAR: binding.token_ref})

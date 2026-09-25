"""Per-session environment an operator agent needs in its runner.

Some agents reach a service that wants a credential, and the credential belongs
to the host the turn runs on rather than to the coordinator. Eva is the case
this was built for: her tools are remote MCP calls to an app on the operator's
own machine, authenticated with that rep's bearer token, and the coordinator
must never hold it.

The mechanism here is the alternative to three things that all work and are all
wrong:

*A host-wide environment variable*, which is what
``OMNIGENT_RUNNER_ENV_PASSTHROUGH`` gives you. It works for one developer on one
Mac and cannot be right for a product, because a host-wide variable holds one
value and each rep has their own token.

*The secret in the coordinator's environment*, so its own bundle expansion
succeeds. Every session of that agent, on every host, then gets that one value.

*The secret in a bundle or a launchd plist*, which this project forbids outright.

## The shape

A provider answers, for one agent and one user, two mappings:

``env``
    Plain values. Not secret. A URL, a label, a feature flag.

``secret_refs``
    Environment variable to a secret *reference* in the form
    :func:`omnigent.onboarding.provider_config.resolve_secret` understands, for
    example ``keychain:eva-outreach-token``. **The reference travels; the value
    does not.** The host resolves it from its own store at launch, so the secret
    never crosses the wire and never exists on the coordinator.

The host applies the result to one runner process, and
``omnigent/host/connect.py`` states the property that makes this safe: one live
runner per session. A value layered into that environment belongs to one user's
turn and cannot reach another's.

## What the host still decides

The host refuses any reference it has not been told to allow, because the
coordinator naming a secret is a request rather than an instruction. The
allowlist is the host owner's, in ``OMNIGENT_HOST_SECRET_REFS``, and it holds
names rather than values. Without it a coordinator could ask a host to read any
entry in its keychain and hand it to an agent that can print its own
environment, which would undo the reason
``omnigent/host/connect.py`` filters the runner environment at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

#: A provider takes the agent's name and the acting user's id and returns
#: :class:`LaunchEnv`, or ``None`` when it has nothing to say about that pair.
Provider = Callable[[str, str | None], "LaunchEnv | None"]

_providers: list[Provider] = []


@dataclass(frozen=True)
class LaunchEnv:
    """What one agent's runner needs in its environment for one session."""

    #: Plain, non-secret values, applied as-is.
    env: dict[str, str] = field(default_factory=dict)

    #: Environment variable to secret reference, resolved on the host.
    secret_refs: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.env and not self.secret_refs


def register(provider: Provider) -> None:
    """Add a provider. Called once at server startup, next to the agent's own
    registration, so an agent that is not registered contributes nothing.

    :param provider: See :data:`Provider`.
    """
    _providers.append(provider)


def reset() -> None:
    """Drop every provider. For tests, which must not leak into each other."""
    _providers.clear()


def collect(agent_name: str | None, user_id: str | None) -> LaunchEnv:
    """Merge every provider's answer for this agent and user.

    :param agent_name: The agent being launched, e.g. ``"eva"``. ``None``
        (no resolvable agent) collects nothing.
    :param user_id: The acting user, e.g. ``"aerickson@airbrx.com"``.
    :returns: The merged :class:`LaunchEnv`, empty when nothing applies.
    :raises ValueError: If two providers claim the same variable. Silently
        letting one win would make which credential a runner gets depend on
        import order.
    """
    if agent_name is None:
        return LaunchEnv()

    env: dict[str, str] = {}
    refs: dict[str, str] = {}
    for provider in _providers:
        answer = provider(agent_name, user_id)
        if answer is None or answer.is_empty():
            continue
        for key, value in answer.env.items():
            if key in env or key in refs:
                raise ValueError(f"two providers both set {key!r} for agent {agent_name!r}")
            env[key] = value
        for key, ref in answer.secret_refs.items():
            if key in env or key in refs:
                raise ValueError(f"two providers both set {key!r} for agent {agent_name!r}")
            refs[key] = ref
    return LaunchEnv(env=env, secret_refs=refs)

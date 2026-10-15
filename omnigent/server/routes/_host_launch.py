"""Reachability-checked resolution for host runner launches.

Two routes spawn a runner subprocess on a user's host machine and
bind it to a session: ``POST /v1/sessions`` (inline host launch) and
``POST /v1/hosts/{host_id}/runners``. A runner executes tools (shell,
file I/O) on the host as that host's user, so a launch must be
authorized against BOTH the host and the session:

- the caller must be able to *reach* the target host — they own it,
  OR the owner opted the host into sharing with ``--shared`` (in which
  case a non-owner's session is confined to the host's ``workroot``;
  see :func:`omnigent.stores.host_store.caller_can_reach_host` /
  ``workroot_jail``). A private host stays owner-only (else a non-owner
  could run code on another user's machine — cross-user RCE), and
- the caller must own the target session (else they could bind their
  runner to another user's session, or another user's host to their
  session — cross-user hijack / data theft).

Centralizing the checks here keeps the two call sites from drifting
(the original bug was each site enforcing a different subset).
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass

from fastapi import HTTPException

from omnigent.entities import Conversation
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_OWNER
from omnigent.server.host_registry import HostConnection, HostRegistry
from omnigent.server.permissions import check_session_access
from omnigent.stores import ConversationStore
from omnigent.stores.host_store import Host, HostStore, caller_can_reach_host, host_is_live
from omnigent.stores.permission_store import PermissionStore

_logger = logging.getLogger(__name__)

LAUNCH_TIMEOUT_ENV_VAR = "OMNIGENT_HOST_LAUNCH_TIMEOUT_S"

# How long a launch waits for the host's result frame before the session is
# failed. A healthy launch only spawns a runner subprocess and answers in
# seconds, so this budget is slack for a busy machine — but a launch that does
# real set-up first (creating a container sandbox, pulling an image, starting
# the app the agent will test) legitimately needs longer, and going over the
# budget fails a session that nothing was actually wrong with. Deployments with
# that kind of set-up raise the bound via LAUNCH_TIMEOUT_ENV_VAR.
_DEFAULT_LAUNCH_TIMEOUT_S = 30.0

# Floor for the override: under a second no launch can answer, so a typo
# ("0", "0.05") would fail every launch rather than widen the budget.
_MIN_LAUNCH_TIMEOUT_S = 1.0

# Ceiling for the override: a launch that never answers must still fail, or
# the create request and its pending launch hang for the process lifetime.
_MAX_LAUNCH_TIMEOUT_S = 3600.0


@functools.cache
def resolve_launch_timeout_s() -> float:
    """Seconds a launch waits for the host's result (env override or default).

    Read from :data:`LAUNCH_TIMEOUT_ENV_VAR`, which both launch routes share so
    the two can't drift. A value that isn't a finite number between
    :data:`_MIN_LAUNCH_TIMEOUT_S` and :data:`_MAX_LAUNCH_TIMEOUT_S` is ignored
    with a warning rather than raised: this runs on the session-create path,
    where rejecting a malformed operator setting would turn one typo into
    every session failing to start.

    Cached, so the warning is logged once and the budget is fixed for the
    process lifetime; a change needs a server restart (tests call
    ``resolve_launch_timeout_s.cache_clear()``).

    :returns: The launch-result timeout in seconds.
    """
    raw = os.environ.get(LAUNCH_TIMEOUT_ENV_VAR)
    if raw is None or not raw.strip():
        return _DEFAULT_LAUNCH_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        _logger.warning(
            "%s must be a number of seconds, got %r — using %gs",
            LAUNCH_TIMEOUT_ENV_VAR,
            raw,
            _DEFAULT_LAUNCH_TIMEOUT_S,
        )
        return _DEFAULT_LAUNCH_TIMEOUT_S
    # Also rejects the "nan" and "inf" that ``float()`` accepts.
    if not _MIN_LAUNCH_TIMEOUT_S <= value <= _MAX_LAUNCH_TIMEOUT_S:
        _logger.warning(
            "%s must be between %gs and %gs, got %r — using %gs",
            LAUNCH_TIMEOUT_ENV_VAR,
            _MIN_LAUNCH_TIMEOUT_S,
            _MAX_LAUNCH_TIMEOUT_S,
            raw,
            _DEFAULT_LAUNCH_TIMEOUT_S,
        )
        return _DEFAULT_LAUNCH_TIMEOUT_S
    return value


@dataclass
class HostLaunchTarget:
    """A host + session pair the caller is authorized to launch on.

    :param host: The persistent host record (owned by the caller).
    :param conn: The live host WebSocket connection on this replica,
        used to send the launch frame.
    :param conv: The session/conversation the runner will bind to.
    """

    host: Host
    conn: HostConnection
    conv: Conversation


def resolve_host_owner(
    *,
    user_id: str | None,
    host_id: str,
    host_store: HostStore,
) -> Host:
    """
    Authorize that the caller may *reach* a known host.

    Every route that reaches a host on the caller's behalf must pass
    this first so the reachability check can't drift between them: the
    runner launch (via :func:`resolve_host_launch`) AND the session-create
    workspace probe, which sends a ``host.stat`` to the host. The
    original bug had that probe contacting another user's host before
    any ownership check.

    Reachability (see :func:`caller_can_reach_host`) is broader than
    ownership: the caller passes if auth is disabled (``user_id`` is
    ``None``), if they own the host, OR if the host is **shared**
    (``visibility == "shared"``) — a shared, always-on host any
    authenticated user may target. It does NOT authorize host
    management (delete/reassign), which keeps its own owner check.

    :param user_id: Authenticated caller, e.g. ``"alice@example.com"``,
        or ``None`` when auth is disabled.
    :param host_id: Target host id, e.g. ``"host_a1b2c3d4..."``.
    :param host_store: Persistent host registrations.
    :returns: The host record the caller may reach.
    :raises HTTPException: 404 if the host is unknown; 403 if it is
        private and owned by a different user.
    """
    host = host_store.get_host(host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    if not caller_can_reach_host(host, user_id):
        raise HTTPException(status_code=403, detail="not your host")
    return host


# Set by ``create_app(host_sharded=...)``. Process-wide because
# :func:`host_absent_error` runs deep in route helpers with no request to read
# ``app.state`` from.
_host_sharded = False


def configure_host_sharding(enabled: bool) -> None:
    """Record whether this deployment shards host traffic across replicas.

    :param enabled: ``True`` when replicas sit behind a router that keys host
        and runner tunnels by host_id, so a host absent from this replica's
        registry may be live on another one.
    """
    global _host_sharded
    _host_sharded = enabled


def _deployment_is_sharded() -> bool:
    """Return whether this deployment shards host traffic by host_id."""
    return _host_sharded


def host_absent_error(host: Host, *, sharded: bool | None = None) -> OmnigentError:
    """Classify a "host not on this replica" miss for a host-scoped route.

    Every route that reaches a host over its live tunnel looks it up in the
    local (this-replica) ``HostRegistry``. When replicas are sharded by host, a
    request keyed to ``host_id`` can land on a replica that doesn't hold the
    tunnel — the same wrong-replica case ``RunnerRouter`` handles for runner
    dispatch. Tell the two apart from the host record the caller already loaded:

    - **sharded** deployment and still **live** (online + fresh heartbeat) → up
      on some replica, just not here → :data:`~ErrorCode.WRONG_REPLICA` (400) so
      the client re-addresses WITHOUT the key.
    - otherwise → genuinely unreachable here → ``CONFLICT`` (409).

    On a single-replica deployment the local registry is authoritative: an
    absent host is unreachable, full stop. Claiming ``WRONG_REPLICA`` there is a
    lie the client can't satisfy — there is no other replica to re-address to,
    so a stale-but-``online`` DB row (a host that went away without a clean
    ``set_offline`` — version swap, crash) would drive an endless 400 poll loop.
    So ``WRONG_REPLICA`` is emitted only when the deployment is actually sharded.

    :param host: The host's persistent record (owner-checked by the caller).
    :param sharded: Whether this deployment shards by host_id. Defaults to the
        deployment's setting (:func:`configure_host_sharding`); overridable for
        tests.
    :returns: The ``OmnigentError`` to raise; the global handler maps its code
        to the HTTP status and the ``{"error": {"code": ...}}`` body the
        client's re-address matches on.
    """
    if sharded is None:
        sharded = _deployment_is_sharded()
    if sharded and host_is_live(host):
        return OmnigentError("host is on another replica", code=ErrorCode.WRONG_REPLICA)
    return OmnigentError("host is offline", code=ErrorCode.CONFLICT)


_EMPTY_LAUNCH_ENV: dict[str, dict[str, str] | None] = {"agent_env": None, "agent_secret_env": None}


def launch_env_fields(
    *,
    agent_id: str | None,
    user_id: str | None,
    host_owner: str | None,
    identity_established: bool = True,
    agent_store: object | None = None,
) -> dict[str, dict[str, str] | None]:
    """The ``agent_env`` / ``agent_secret_env`` of a runner launch frame.

    **Every site that builds a** :class:`~omnigent.host.frames.HostLaunchRunnerFrame`
    **spreads this into it**, and ``tests/server/test_launch_env_every_path.py``
    fails the build if one does not. The first version of the mechanism (#78)
    collected the environment at one launch site of three: session create with
    a host. ``POST /v1/hosts/{id}/runners`` (resume, switch host, fork) and the
    relaunch that runs when a message arrives for a session whose runner is gone
    sent nothing, and nothing in the suite noticed because the one test covered
    the one site that worked.

    **Sent only to the host owner's own runners.** A secret reference is
    resolved on the host from the host's own store, so whatever it names is the
    host OWNER's secret. On a host shared with other users (``--shared``), a
    non-owner's session would otherwise receive the owner's credential and act
    as the owner in whatever that credential opens: for Eva, the owner's
    outreach account. So the environment goes out only when the acting user is
    the host owner; otherwise nothing is sent and the reason is logged, and the
    agent fails turn setup for want of a credential rather than running with
    someone else's. A missing credential is an error; a wrong one is an
    incident.

    :param agent_id: The session's agent, e.g. ``conv.agent_id``. ``None``
        (no resolvable agent) collects nothing.
    :param user_id: The user the runner acts for, as established by the caller.
        ``None`` only when auth is disabled (single-user server), which is
        allowed: there is no other user whose credential could be sent.
    :param host_owner: The host's owner (``Host.user_id`` or
        ``HostConnection.owner``). ``None`` only when auth is disabled.
    :param identity_established: ``False`` when the caller could not establish
        who the runner acts for. The relaunch path passes it on a shared host,
        where a session's owner is not knowable from the host connection; the
        environment is then withheld.
    :param agent_store: Store to resolve ``agent_id`` to a name. ``None`` uses
        the runtime's store; a server whose runtime is not initialized
        collects nothing. Any other store failure propagates.
    :returns: ``{"agent_env": ..., "agent_secret_env": ...}``, each ``None``
        when empty, ready to spread into the frame.
    """
    from omnigent.runtime.launch_env import collect

    if agent_id is None:
        return dict(_EMPTY_LAUNCH_ENV)
    store = agent_store
    if store is None:
        from omnigent.runtime import get_agent_store

        try:
            store = get_agent_store()
        except RuntimeError:
            return dict(_EMPTY_LAUNCH_ENV)
    row = store.get(agent_id)  # type: ignore[attr-defined]
    agent_name = getattr(row, "name", None) if row is not None else None
    if agent_name is None:
        return dict(_EMPTY_LAUNCH_ENV)

    acting = user_id if identity_established else host_owner
    launch = collect(agent_name, acting)
    if launch.is_empty():
        return dict(_EMPTY_LAUNCH_ENV)
    if not identity_established or (user_id is not None and user_id != host_owner):
        _logger.warning(
            "launch environment for agent %r withheld: the runner acts for %s on a host "
            "owned by %r, and a secret reference resolves from the host owner's own store. "
            "The agent will fail turn setup for want of its credential.",
            agent_name,
            repr(user_id) if identity_established else "a user this launch path cannot establish",
            host_owner,
        )
        return dict(_EMPTY_LAUNCH_ENV)
    return {
        "agent_env": launch.env or None,
        "agent_secret_env": launch.secret_refs or None,
    }


def resolve_host_launch(
    *,
    user_id: str | None,
    host_id: str,
    session_id: str,
    host_store: HostStore,
    host_registry: HostRegistry,
    conversation_store: ConversationStore,
    permission_store: PermissionStore | None,
    conversation: Conversation | None = None,
) -> HostLaunchTarget:
    """
    Resolve and authorize a host runner launch.

    Verifies the host exists, is owned by the caller, and is online,
    and that the caller owns the target session, before any runner is
    spawned. When ``user_id`` is ``None`` (auth disabled) the host-owner
    check is skipped; when ``permission_store`` is ``None`` (auth
    disabled) the session-owner check is skipped — both consistent with
    the single-user/local deployment behavior elsewhere.

    :param user_id: Authenticated caller, e.g. ``"alice@example.com"``,
        or ``None`` when auth is disabled.
    :param host_id: Target host id, e.g. ``"host_a1b2c3d4..."``.
    :param session_id: Session to bind the runner to, e.g.
        ``"conv_abc123"``.
    :param host_store: Persistent host registrations.
    :param host_registry: In-memory live host connections (this replica).
    :param conversation_store: Conversation lookups (also used by the
        session-access check for sub-agent parent delegation).
    :param permission_store: Session permission store, or ``None`` to
        skip the session-owner check (auth disabled).
    :param conversation: Optional authoritative row already loaded for the
        target session. Its id must match ``session_id``.
    :returns: A :class:`HostLaunchTarget` with the validated host,
        connection, and conversation.
    :raises HTTPException: 404 if the host or session is missing (or the
        session is not owned by the caller — 404, not 403, so other
        users' sessions aren't enumerable); 403 if the host is owned by
        a different user.
    :raises OmnigentError: 409 (CONFLICT) if the host is genuinely offline,
        or 400 (WRONG_REPLICA) if it is live but its tunnel is on another
        replica (a wrong-replica landing — the caller re-addresses keyless).
    """
    host = resolve_host_owner(
        user_id=user_id,
        host_id=host_id,
        host_store=host_store,
    )

    conn = host_registry.get(host_id)
    if conn is None:
        raise host_absent_error(host)

    if conversation is not None and conversation.id != session_id:
        raise HTTPException(status_code=404, detail="session not found")
    conv = conversation or conversation_store.get_conversation(session_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="session not found")

    # A runner executes tools as the session's driver, so only the
    # session owner may bind one. A non-owner has no owner-level grant
    # and is rejected. 404 (not 403) avoids leaking the existence of
    # other users' sessions.
    if permission_store is not None:
        allowed = check_session_access(
            user_id,
            session_id,
            LEVEL_OWNER,
            permission_store,
            conversation_store,
            conversation=conv,
        )
        if not allowed:
            raise HTTPException(status_code=404, detail="session not found")

    return HostLaunchTarget(host=host, conn=conn, conv=conv)

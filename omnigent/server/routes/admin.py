"""Admin routes: user list + an admin's view of any user's sessions.

These power the OIDC/SSO admin surface — where the accounts-mode
``Members`` page is not rendered, an operator still needs to see who
has accounts and browse their sessions. Every route here is gated on
the caller's ``is_admin`` flag (the same boolean the rest of the
server uses); this is intentionally *not* a role system.

Admins already hold owner-level access to any individual session
(``check_session_access`` short-circuits for admins), so once a
session id is listed here the existing session routes let the admin
open and act on it. These routes only add *discovery*: enumerate
users, and enumerate a chosen user's sessions.
"""

from __future__ import annotations

import asyncio
import logging
import os
from importlib.metadata import version as _pkg_version

from fastapi import APIRouter, Query, Request

from omnigent.entities import Conversation, SessionPermission
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import LEVEL_OWNER, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.stores.conversation_store import ConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.update_check import version_label

_logger = logging.getLogger(__name__)

# Numeric permission level → role label shown to admins.
_ROLE_NAMES = {1: "read", 2: "edit", 3: "manage", 4: "owner"}


def _owner_of(grants: list[SessionPermission]) -> str | None:
    """The owner (highest-level grantee at or above ``LEVEL_OWNER``), or None."""
    owners = [g for g in grants if g.level >= LEVEL_OWNER]
    if not owners:
        return None
    return max(owners, key=lambda g: g.level).user_id


def _role_for(grants: list[SessionPermission], user_id: str) -> str | None:
    """The role label for ``user_id`` on a session, from its grants."""
    levels = [g.level for g in grants if g.user_id == user_id]
    if not levels:
        return None
    return _ROLE_NAMES.get(max(levels))


def _build_session_rows(
    convs: list[Conversation],
    *,
    permission_store: PermissionStore,
    host_store: HostStore | None,
    role_for: str | None,
) -> list[dict[str, object]]:
    """Build admin session rows (owner, role, cost/tokens, bound host).

    Shared by the per-user and the global sessions listings.

    :param convs: The conversations to render.
    :param role_for: When set, each row's ``role`` / ``is_owner`` is computed
        relative to this user (the per-user view); ``None`` for the global
        view, where ``role`` is ``None`` and ``is_owner`` is ``False``.
    """
    grants_by_conv = permission_store.list_for_sessions([c.id for c in convs])
    # Resolve each session's bound host to a name + liveness. Distinct host ids
    # are few, so a small map over the distinct set avoids a per-session lookup.
    host_ids = {c.host_id for c in convs if c.host_id}
    hosts_by_id = {}
    online_hosts: set[str] = set()
    if host_store is not None and host_ids:
        for hid in host_ids:
            host = host_store.get_host(hid)
            if host is not None:
                hosts_by_id[hid] = host
        online_hosts = host_store.online_host_ids(list(host_ids))
    rows: list[dict[str, object]] = []
    for c in convs:
        grants = grants_by_conv.get(c.id, [])
        owner = _owner_of(grants)
        # Prefer the host's friendly name; fall back to the raw id for a host
        # that's been deleted but still bound on the session.
        host_label = None
        if c.host_id:
            known = hosts_by_id.get(c.host_id)
            host_label = known.name if known is not None else c.host_id
        rows.append(
            {
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
                "cost_usd": float(c.session_usage.get("total_cost_usd") or 0.0),
                "total_tokens": int(c.session_usage.get("total_tokens") or 0),
                "role": _role_for(grants, role_for) if role_for is not None else None,
                "owner": owner,
                "is_owner": owner == role_for if role_for is not None else False,
                "host": host_label,
                "host_online": c.host_id in online_hosts if c.host_id else False,
            }
        )
    return rows


def create_admin_router(
    permission_store: PermissionStore,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    host_store: HostStore | None = None,
) -> APIRouter:
    """Build the admin router (mounted under ``/v1``).

    :param permission_store: Backs the admin check and the user list.
    :param conversation_store: Backs the per-user session listing.
    :param auth_provider: Resolves the caller identity from the
        request. ``None`` in single-user mode (admin routes are then
        effectively unreachable — there is no multi-user surface).
    :param host_store: Backs the per-user host counts on the user list.
        ``None`` when host support is not wired — host counts are then
        reported as zero rather than failing the request.
    :returns: An :class:`APIRouter` with the admin discovery routes.
    """
    router = APIRouter()

    async def _require_admin(request: Request) -> str:
        """Authn + authz: resolve the caller and require ``is_admin``.

        :raises OmnigentError: 401 if unauthenticated, 403 if the
            authenticated user is not an admin.
        """
        user_id = get_user_id(request, auth_provider)
        if user_id is None:
            raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
        if not await asyncio.to_thread(permission_store.is_admin, user_id):
            raise OmnigentError(
                "Admin privileges required",
                code=ErrorCode.FORBIDDEN,
            )
        return user_id

    @router.get("/admin/users")
    async def list_users(request: Request) -> dict[str, object]:
        """List real users (admin only), each with an owned-usage rollup.

        ``cost_usd`` / ``total_tokens`` / ``session_count`` cover the
        sessions the user OWNS — cost is attributed to the owner, so a
        user merely invited to a session is not credited its cost.

        **Invite-only phantoms are hidden.** A row is created in the
        ``users`` table whenever someone is granted access to a session
        (the grant's FK requires it), even if that person never logged
        in. Such an account — not an admin, owns no session, but holds an
        invite (read/edit/manage) grant — is omitted here; ``hidden``
        reports how many were filtered. A real user who logged in but has
        not created anything (no grants at all) is kept, as are admins.

        ``host_count`` / ``online_host_count`` are the hosts the user
        owns (all registered, and the live subset) — zero when host
        support is not wired.

        :returns: ``{"users": [{"user_id", "is_admin", "cost_usd",
            "total_tokens", "session_count", "host_count",
            "online_host_count"}, ...], "hidden": N}``.
        """
        await _require_admin(request)

        def _build() -> dict[str, object]:
            out: list[dict[str, object]] = []
            hidden = 0
            for u in permission_store.list_users():
                totals = conversation_store.usage_totals_for_user(u.id)
                # Hide invite-only phantoms: own nothing, hold only invite
                # grants, not an admin. Skip the grant lookup for users who
                # already own a session (the common case).
                if not u.is_admin and totals.session_count == 0:
                    grants = permission_store.list_for_user(u.id)
                    owns = any(g.level >= LEVEL_OWNER for g in grants)
                    invited = any(g.level < LEVEL_OWNER for g in grants)
                    if not owns and invited:
                        hidden += 1
                        continue
                # Per-user host inventory (cheap at admin-list scale; the
                # online subset reuses the same liveness gate as the sidebar).
                hosts = host_store.list_hosts(u.id) if host_store is not None else []
                online = (
                    host_store.online_host_ids([h.host_id for h in hosts])
                    if host_store is not None and hosts
                    else set()
                )
                out.append(
                    {
                        "user_id": u.id,
                        "is_admin": u.is_admin,
                        "cost_usd": totals.cost_usd,
                        "total_tokens": totals.total_tokens,
                        "session_count": totals.session_count,
                        "host_count": len(hosts),
                        "online_host_count": len(online),
                    }
                )
            return {"users": out, "hidden": hidden}

        return await asyncio.to_thread(_build)

    @router.get("/admin/users/{user_id}/sessions")
    async def list_user_sessions(
        request: Request,
        user_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        """List the sessions a given user can access (admin only).

        Uses the same ``accessible_by`` filter the user's own session
        list uses, so an admin sees exactly what that user would —
        top-level (``kind="default"``) sessions only.

        :param user_id: The user whose sessions to list, e.g.
            ``"alice@example.com"``.
        :param limit: Maximum sessions to return (1–500).
        :returns: ``{"user_id", "totals": {...}, "sessions": [{"id",
            "title", "created_at", "updated_at", "cost_usd",
            "total_tokens", "role", "owner", "is_owner", "host",
            "host_online"}, ...]}``. ``role`` is the user's level on that
            session (owner / manage / edit / read); ``owner`` is the
            session's owner. ``host`` is the friendly name of the host the
            session is bound to (the raw id if that host was deleted,
            ``None`` if unbound); ``host_online`` is its liveness.
            Per-session cost/tokens are the session's; ``totals`` is the
            user's OWNED-session rollup (cost attributed to the owner), so
            a session the user was merely invited to does not count toward
            their total.
        """
        await _require_admin(request)

        def _build() -> dict[str, object]:
            paged = conversation_store.list_conversations(accessible_by=user_id, limit=limit)
            totals = conversation_store.usage_totals_for_user(user_id)
            sessions = _build_session_rows(
                paged.data,
                permission_store=permission_store,
                host_store=host_store,
                role_for=user_id,
            )
            return {
                "user_id": user_id,
                "totals": {
                    "cost_usd": totals.cost_usd,
                    "total_tokens": totals.total_tokens,
                    "session_count": totals.session_count,
                },
                "sessions": sessions,
            }

        return await asyncio.to_thread(_build)

    @router.get("/admin/sessions")
    async def list_sessions(
        request: Request,
        user: str | None = Query(default=None),
        host: str | None = Query(default=None),
        q: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        """List sessions across users (admin only), with optional filters.

        Powers the global admin Sessions view and the cross-links from the
        Users / Hosts views. Top-level (``kind="default"``) sessions only.

        :param user: Restrict to sessions this user can access (their
            ``accessible_by`` set); also sets each row's ``role``/``is_owner``.
        :param host: Restrict to sessions bound to this host id.
        :param q: Case-insensitive title substring filter.
        :param limit: Maximum sessions to return (1–500).
        :returns: ``{"sessions": [{...same shape as the per-user listing...}]}``.
        """
        await _require_admin(request)

        def _build() -> dict[str, object]:
            if host is not None:
                convs = [
                    c
                    for c in conversation_store.list_conversations_by_host_id(host)
                    if c.kind == "default"
                ]
                if user is not None:
                    accessible = {
                        c.id
                        for c in conversation_store.list_conversations(
                            accessible_by=user, limit=500
                        ).data
                    }
                    convs = [c for c in convs if c.id in accessible]
                if q:
                    needle = q.lower()
                    convs = [c for c in convs if c.title and needle in c.title.lower()]
                convs = sorted(convs, key=lambda c: c.updated_at, reverse=True)[:limit]
            else:
                convs = conversation_store.list_conversations(
                    accessible_by=user, search_query=q, limit=limit
                ).data
            rows = _build_session_rows(
                convs,
                permission_store=permission_store,
                host_store=host_store,
                role_for=user,
            )
            return {"sessions": rows}

        return await asyncio.to_thread(_build)

    @router.get("/admin/hosts")
    async def list_hosts(
        request: Request,
        user: str | None = Query(default=None),
        status: str | None = Query(default=None),
        version: str | None = Query(default=None),
    ) -> dict[str, object]:
        """List hosts across users (admin only), with optional filters.

        :param user: Restrict to hosts owned by this user.
        :param status: ``"online"`` or ``"offline"`` (computed via the same
            liveness gate the sidebar uses, not the raw stored status).
        :param version: Restrict to hosts reporting this exact version.
        :returns: ``{"hosts": [{"host_id", "name", "owner", "online",
            "version", "os", "outdated", "login_token_expires_at",
            "harnesses", "last_seen", "created_at"}, ...]}``,
            most-recently-active first.
        """
        await _require_admin(request)

        def _build() -> dict[str, object]:
            hosts = host_store.list_all_hosts() if host_store is not None else []
            if user is not None:
                hosts = [h for h in hosts if h.user_id == user]
            online = (
                host_store.online_host_ids([h.host_id for h in hosts])
                if host_store is not None and hosts
                else set()
            )
            # The host is "outdated" when its reported version label differs
            # from the build this server runs (None when the host reported no
            # version — an older build that predates version reporting).
            server_label = version_label()
            rows: list[dict[str, object]] = []
            for h in hosts:
                is_online = h.host_id in online
                if status == "online" and not is_online:
                    continue
                if status == "offline" and is_online:
                    continue
                if version is not None and (h.version or "") != version:
                    continue
                rows.append(
                    {
                        "host_id": h.host_id,
                        "name": h.name,
                        "owner": h.user_id,
                        "online": is_online,
                        "version": h.version,
                        "os": h.os,
                        "outdated": (h.version != server_label) if h.version else None,
                        "login_token_expires_at": h.login_token_expires_at,
                        "harnesses": h.configured_harnesses,
                        "last_seen": h.updated_at,
                        "created_at": h.created_at,
                    }
                )
            return {"hosts": rows}

        return await asyncio.to_thread(_build)

    @router.get("/admin/server")
    async def server_info(request: Request) -> dict[str, object]:
        """Server version + host-upgrade info for the admin header (admin only).

        ``version_label`` is the build-identity string hosts are compared
        against. ``install_command`` is the one-liner that upgrades a host to
        exactly this server's version (served by ``GET /install.sh``); it is
        ``None`` when ``OMNIGENT_DOMAIN`` is unset (no public URL to curl).

        :returns: ``{"version", "commit", "built_at", "version_label",
            "install_command"}``.
        """
        await _require_admin(request)
        commit: str | None = None
        built_at: int | None = None
        try:
            from omnigent import _build_info

            commit = _build_info.COMMIT_SHA or None
            built_at = _build_info.BUILD_TIME_EPOCH
        except (ImportError, AttributeError):  # source checkout that was never built
            pass
        domain = os.environ.get("OMNIGENT_DOMAIN", "").strip()
        install_command = f"curl -fsSL https://{domain}/install.sh | sh" if domain else None
        return {
            "version": _pkg_version("omnigent"),
            "commit": commit,
            "built_at": built_at,
            "version_label": version_label(),
            "install_command": install_command,
        }

    return router

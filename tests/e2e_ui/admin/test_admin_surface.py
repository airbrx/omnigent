"""e2e_ui: the OIDC/SSO admin surface at ``/admin``.

End-to-end through the spawned server + built SPA: an admin loads the
user list, sees a member with their owned-host count, drills into that
member, and reads back the member's session with its bound host.

The e2e_ui ``live_server`` is single-user (the headerless ``local``
identity) but honours the ``X-Forwarded-Email`` header, so we drive
distinct identities the same way the collaboration suite does. Two facts
shape the seeding:

- ``local`` is never an admin (the admin signal is a DB flag, and no
  login/promote path fires in header mode) and is excluded from the user
  list — so the admin must be a *header-identified* user seeded
  ``is_admin`` directly in the server's SQLite DB.
- The admin flag and a session's host binding have no public API, so the
  whole scenario is seeded directly against the DB (``_server_state``
  exposes its path); the browser then only *reads it back*, which is what
  this test is asserting.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from playwright.sync_api import Browser, expect

from tests.e2e_ui.conftest import _server_state


@dataclass
class _AdminScenario:
    admin_email: str
    member_email: str
    host_name: str
    host_version: str
    host_os: str
    session_title: str


@pytest.fixture
def admin_scenario(live_server: str) -> Iterator[_AdminScenario]:
    """Seed an admin, a member, and the member's host-bound session.

    Direct DB writes against the spawned server's SQLite file: the admin
    flag and the host binding have no API. Unique per-run emails keep this
    independent of rows other tests left in the session-scoped DB.
    """
    from omnigent.server.auth import LEVEL_OWNER
    from omnigent.stores.conversation_store.sqlalchemy_store import (
        SqlAlchemyConversationStore,
    )
    from omnigent.stores.host_store import HostStore
    from omnigent.stores.permission_store.sqlalchemy_store import (
        SqlAlchemyPermissionStore,
    )

    db_uri = f"sqlite:///{_server_state['db_path']}"
    suffix = uuid.uuid4().hex[:6]
    scenario = _AdminScenario(
        admin_email=f"admin-{suffix}@ui.test",
        member_email=f"member-{suffix}@ui.test",
        host_name=f"member-laptop-{suffix}",
        host_version="9.9.9",
        host_os="Darwin 99.9 (arm64)",
        session_title=f"Member session {suffix}",
    )

    perms = SqlAlchemyPermissionStore(db_uri)
    convs = SqlAlchemyConversationStore(db_uri)
    hosts = HostStore(db_uri)

    # Admin: the only admin signal in header mode is the DB flag.
    perms.ensure_user(scenario.admin_email, is_admin=True)
    perms.set_admin(scenario.admin_email, True)

    # Member with one owned, titled session bound to an online host.
    perms.ensure_user(scenario.member_email)
    conv = convs.create_conversation()
    convs.update_conversation(conv.id, title=scenario.session_title)
    perms.grant(scenario.member_email, conv.id, level=LEVEL_OWNER)
    host_id = uuid.uuid4().hex
    hosts.upsert_on_connect(
        host_id,
        scenario.host_name,
        scenario.member_email,
        version=scenario.host_version,
        os=scenario.host_os,
    )
    convs.set_host_id(conv.id, host_id, workspace="/w")

    yield scenario


def test_admin_three_views(
    browser: Browser, live_server: str, admin_scenario: _AdminScenario
) -> None:
    """Admin walks Users → Sessions → Hosts and reads the seeded scenario back."""
    # The extra header rides every fetch/XHR, so /v1/me resolves the admin
    # identity and the SPA renders the admin chrome.
    ctx = browser.new_context(extra_http_headers={"X-Forwarded-Email": admin_scenario.admin_email})
    member = admin_scenario.member_email
    try:
        page = ctx.new_page()

        # Members: the admin user list folded into Settings ▸ Admin. The
        # per-user usage rollup (the "1 · 1 online" table) only renders in a
        # multi-user deploy — MembersPage is deliberately gated off in
        # single-user mode, which is what this header-identified live_server
        # runs. So we assert that single-user state here; the seeded scenario
        # is read back via the Sessions/Hosts views below, which gate on
        # is_admin alone and do render in single-user mode.
        page.goto(f"{live_server}/settings/members")
        expect(
            page.get_by_text("Member management is not available in single-user mode.")
        ).to_be_visible(timeout=20_000)

        # Sessions filtered by the member: the host-bound session, with its host.
        page.goto(f"{live_server}/settings/sessions?user={member}")
        session_row = page.get_by_test_id("admin-session-row").filter(
            has_text=admin_scenario.session_title
        )
        expect(session_row).to_be_visible(timeout=10_000)
        expect(session_row).to_contain_text(admin_scenario.host_name)

        # Hosts filtered by the member: the host with its persisted version.
        page.goto(f"{live_server}/settings/hosts?user={member}")
        host_row = page.get_by_test_id("admin-host-row").filter(has_text=admin_scenario.host_name)
        expect(host_row).to_be_visible(timeout=10_000)
        expect(host_row).to_contain_text(admin_scenario.host_version)
        expect(host_row).to_contain_text(admin_scenario.host_os)
    finally:
        ctx.close()

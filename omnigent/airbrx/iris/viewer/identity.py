"""Who is looking at the viewer (STANDALONE_VIEWER.md, section 4.3).

`DevIdentity` is the only provider built: every request is the one dev email,
with no password and no cookie, and it is allowed only on a loopback bind
(rule V5). `SsoIdentity` for iris.airbrx.ai (JumpCloud OIDC, verified email,
the airbrx.com / airbrx.ai allow list, tenants from an access file) is named by
the contract and deliberately not built here.
"""

from __future__ import annotations

from typing import NamedTuple, Protocol
from urllib.parse import urlsplit

#: The only binds dev sign-in is allowed on.
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
DEFAULT_DEV_EMAIL = "dev@localhost"


class Identity(NamedTuple):
    kind: str
    email: str | None


class IdentityProvider(Protocol):
    def resolve(self, request) -> Identity | None:
        """The caller, or None: 401 for the API, the sign-in page for pages."""

    def tenants(self, identity: Identity) -> set[str] | None:
        """The tenants `identity` may see, or None for every tenant in the roots."""


class DevIdentity:
    """Every request is `email`. Only ever constructed after `check_dev_bind`."""

    kind = "dev"
    sign_in = None

    def __init__(self, email: str = DEFAULT_DEV_EMAIL):
        self.email = email

    def resolve(self, request) -> Identity:  # noqa: ARG002 (every request is the dev email)
        return Identity(self.kind, self.email)

    def tenants(self, identity: Identity) -> set[str] | None:  # noqa: ARG002
        return None


def check_dev_bind(host: str, *, public_url: str | None) -> None:
    """Refuse to start dev sign-in anywhere but loopback, as outreach's `check_deployable()`.

    Raises SystemExit with the reason, so the process never listens.
    """
    if host not in LOOPBACK:
        raise SystemExit(
            f"Dev sign-in is allowed only on a loopback bind (127.0.0.1, ::1, localhost), "
            f"not {host!r}."
        )
    if public_url and urlsplit(public_url).scheme == "https":
        raise SystemExit(
            f"Dev sign-in cannot serve an https public URL ({public_url}); "
            "a public viewer needs real sign-in."
        )

"""Fork-local store mapping an agent name to an avatar image.

airbrx-only. The image BYTES live in the existing artifact store — which
already has local, S3 and Databricks-volumes backends, so avatars work on
every deployment shape without a new storage mechanism. This module owns
only the mapping row and keeps the two in step.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass

from sqlalchemy import Engine, select

from omnigent.db.db_models import SqlAgentAvatar, current_workspace_id
from omnigent.db.utils import get_or_create_engine, make_named_managed_session_maker
from omnigent.stores.artifact_store import ArtifactStore


@dataclass(frozen=True)
class AgentAvatar:
    """Metadata for one agent's avatar (no bytes)."""

    agent_name: str
    content_type: str
    updated_at: int


def _artifact_key(workspace_id: int, agent_name: str) -> str:
    """Stable artifact key for an agent's avatar."""
    return f"agent-avatars/{workspace_id}/{agent_name}"


class AgentAvatarStore:
    """Avatar metadata in SQL, avatar bytes in the artifact store.

    :param storage_location: SQLAlchemy database URI.
    :param artifact_store: Blob backend for the image bytes.
    """

    def __init__(self, storage_location: str, artifact_store: ArtifactStore) -> None:
        self._engine: Engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.agent_avatar_store",
        )
        self._artifacts = artifact_store

    def put(self, agent_name: str, data: bytes, content_type: str) -> AgentAvatar:
        """Store (or replace) an agent's avatar.

        Bytes are written BEFORE the row is committed: a blob with no row
        is invisible and harmless, whereas a row pointing at a missing
        blob would render as a broken image.
        """
        workspace_id = current_workspace_id()
        key = _artifact_key(workspace_id, agent_name)
        self._artifacts.put(key, data)
        now = int(time.time())
        with self._session("put_agent_avatar") as session:
            row = session.get(SqlAgentAvatar, (workspace_id, agent_name))
            if row is None:
                row = SqlAgentAvatar(
                    workspace_id=workspace_id,
                    agent_name=agent_name,
                    artifact_key=key,
                    content_type=content_type,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.artifact_key = key
                row.content_type = content_type
                row.updated_at = now
        return AgentAvatar(agent_name, content_type, now)

    def get(self, agent_name: str) -> tuple[bytes, AgentAvatar] | None:
        """Return ``(bytes, metadata)``, or ``None`` when unset.

        A row whose blob has gone missing is treated as unset rather than
        raising — the caller renders the initials fallback.
        """
        with self._session("get_agent_avatar") as session:
            row = session.get(SqlAgentAvatar, (current_workspace_id(), agent_name))
            if row is None:
                return None
            meta = AgentAvatar(row.agent_name, row.content_type, row.updated_at)
            key = row.artifact_key
        try:
            return self._artifacts.get(key), meta
        except KeyError:
            return None

    def list_all(self) -> list[AgentAvatar]:
        """Every avatar in this workspace, for the drawer's one-shot fetch."""
        with self._session("list_agent_avatars") as session:
            rows = session.execute(
                select(SqlAgentAvatar)
                .where(SqlAgentAvatar.workspace_id == current_workspace_id())
                .order_by(SqlAgentAvatar.agent_name.asc())
            ).scalars()
            return [AgentAvatar(r.agent_name, r.content_type, r.updated_at) for r in rows]

    def delete(self, agent_name: str) -> bool:
        """Remove an avatar. ``False`` when there was none."""
        workspace_id = current_workspace_id()
        with self._session("delete_agent_avatar") as session:
            row = session.get(SqlAgentAvatar, (workspace_id, agent_name))
            if row is None:
                return False
            key = row.artifact_key
            session.delete(row)
        with contextlib.suppress(KeyError):
            self._artifacts.delete(key)
        return True

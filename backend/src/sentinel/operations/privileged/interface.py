"""The future executor boundary. Release 1 has no implementation that connects to Oracle."""

from typing import Protocol

from sqlalchemy.orm import Session

from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller


class PrivilegedExecutor(Protocol):
    """A reviewed service would implement this later. This package does not."""

    def execute(self, session: Session, settings: Settings, caller: Caller, proposal_id: str) -> None:
        """Run one approved change. Release 1 provides no such method."""

"""Request data available to a tool. Tools cannot open their own Oracle connection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller

if TYPE_CHECKING:
    from sentinel.ai.activity import ActivityReading


@dataclass
class ToolContext:
    session: Session
    settings: Settings
    caller: Caller
    target_id: uuid.UUID | None
    incident_id: uuid.UUID | None
    activity: ActivityReading | None = None

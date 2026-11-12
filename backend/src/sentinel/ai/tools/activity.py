"""Format a database reading that the orchestrator already collected."""

from sentinel.ai.activity import reading_outcome
from sentinel.ai.tools.context import ToolContext
from sentinel.ai.types import ToolOutcome


def database_activity(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    del arguments
    reading = context.activity
    if reading is None:
        return ToolOutcome("database_activity", "denied", "No database reading was requested.", reason="not_requested")
    target_id = str(context.target_id) if context.target_id else None
    return reading_outcome(reading, target_id)

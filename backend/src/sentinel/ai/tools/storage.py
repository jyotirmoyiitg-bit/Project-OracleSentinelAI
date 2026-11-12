"""Tablespace and fast-recovery-area readings from the latest collected observations."""

from sqlalchemy import select

from sentinel.ai.safety import sanitize_untrusted, tablespace_name
from sentinel.ai.tools.context import ToolContext
from sentinel.ai.tools.health import _target
from sentinel.ai.types import EvidenceCitation, ToolOutcome
from sentinel.persistence.models import CurrentObservation


def tablespace_status(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    target = _target(context)
    raw_name = arguments.get("tablespace_name")
    requested = tablespace_name(raw_name if isinstance(raw_name, str) else None)
    rows = context.session.scalars(
        select(CurrentObservation).where(
            CurrentObservation.target_id == target.id,
            CurrentObservation.metric_name.like("storage.tablespace.%"),
        ).limit(40),
    ).all()
    selected = [row for row in rows if _matches(row, requested)]
    items = [_reading(row) for row in selected[:20]]
    label = requested or "all collected tablespaces"
    summary = f"{len(items)} tablespace readings for {label}."
    citations = [
        EvidenceCitation("metric", item["tablespace"], item["metric_name"], item["detail"], str(target.id))
        for item in items[:8]
    ]
    return ToolOutcome("get_tablespace_status", "allowed", summary, citations, {"tablespaces": items})


def fra_status(context: ToolContext, arguments: dict[str, object]) -> ToolOutcome:
    del arguments
    target = _target(context)
    rows = context.session.scalars(
        select(CurrentObservation).where(
            CurrentObservation.target_id == target.id,
            CurrentObservation.metric_name.like("storage.fra.%")
            | CurrentObservation.metric_name.like("storage.archive.%"),
        ).limit(20),
    ).all()
    items = [_reading(row) for row in rows]
    summary = f"{len(items)} fast recovery area readings for {target.display_name}."
    citations = [
        EvidenceCitation("metric", "FRA", item["metric_name"], item["detail"], str(target.id))
        for item in items[:8]
    ]
    return ToolOutcome("get_fra_status", "allowed", summary, citations, {"fra": items})


def _matches(row: CurrentObservation, requested: str | None) -> bool:
    if requested is None:
        return True
    labels = row.labels if isinstance(row.labels, dict) else {}
    name = str(labels.get("tablespace_name") or labels.get("tablespace") or "")
    return name.upper() == requested.upper()


def _reading(row: CurrentObservation) -> dict[str, str]:
    labels = row.labels if isinstance(row.labels, dict) else {}
    name = str(labels.get("tablespace_name") or labels.get("tablespace") or "FRA")
    value = row.value_str if row.value is None else str(row.value)
    detail = sanitize_untrusted(f"{row.metric_name} {name} {value} {row.unit or ''}".strip(), 240)
    return {"metric_name": row.metric_name, "tablespace": name, "value": value or "", "detail": detail}

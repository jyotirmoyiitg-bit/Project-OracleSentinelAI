"""Canonical observations shared by synthetic and Oracle collection."""

import json
import math
import re
from dataclasses import replace
from datetime import UTC, datetime

from sentinel.domain.models import Observation

PROBES = ("connectivity", "instance", "storage", "fra_archive", "resources", "configuration")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def normalize(observations: list[Observation], target_id: str, probe: str) -> list[Observation]:
    if len(observations) > 5000:
        raise ValueError("A probe cannot produce more than 5000 observations")
    normalized = []
    seen = set()
    for obs in observations:
        if not re.fullmatch(r"[a-z][a-z0-9_.]{0,254}", obs.metric):
            raise ValueError(f"Invalid metric name: {obs.metric}")
        if obs.quality not in {"measured", "estimated", "stale"}:
            raise ValueError(f"Invalid observation quality: {obs.quality}")
        if isinstance(obs.value, float) and not math.isfinite(obs.value):
            raise ValueError(f"Non-finite measurement: {obs.metric}")
        labels = dict(sorted(obs.labels.items()))
        if "tablespace" in labels:
            labels["tablespace_name"] = labels.pop("tablespace")
        identity = (obs.metric, json.dumps(labels, sort_keys=True))
        if identity in seen:
            raise ValueError(f"Duplicate observation identity: {obs.metric}")
        seen.add(identity)
        unit = obs.unit
        if obs.metric.endswith(("_pct", "usage_by_type")):
            unit = "percent"
        elif any(part in obs.metric for part in ("bytes_", "space_limit", "space_used", "space_reclaimable")):
            unit = "bytes"
        elif isinstance(obs.value, (int, float)):
            unit = unit or "count"
        normalized.append(replace(
            obs, database_id=target_id, labels=labels, unit=unit,
            collected_at=naive_utc(obs.collected_at),
            source=obs.source or labels.get("source", probe),
        ))
    return normalized

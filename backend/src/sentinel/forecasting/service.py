"""Build per-target tablespace and FRA forecasts from retained metric history."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from sentinel.collectors.normalization import naive_utc
from sentinel.config.settings import Settings
from sentinel.forecasting.regression import ForecastConfig, ForecastPoint, forecast_capacity
from sentinel.persistence.models import CurrentObservation, Observation


def load_forecast_config() -> ForecastConfig:
    path = Path(__file__).resolve().parents[4] / "policies" / "forecasting.yaml"
    with path.open(encoding="utf-8") as config_file:
        raw: Any = yaml.safe_load(config_file)
    if not isinstance(raw, dict):
        raise TypeError(f"Invalid forecast configuration: {path}")
    config = ForecastConfig(
        minimum_points=int(raw.get("minimum_points", 5)),
        minimum_span_hours=float(raw.get("minimum_span_hours", 24)),
        maximum_gap_multiplier=float(raw.get("maximum_gap_multiplier", 3)),
        maximum_observation_age_hours=float(raw.get("maximum_observation_age_hours", 72)),
        early_warning_days=float(raw.get("early_warning_days", 30)),
        prediction_horizon_days=float(raw.get("prediction_horizon_days", 90)),
    )
    if (
        config.minimum_points < 3 or config.minimum_span_hours <= 0 or config.maximum_gap_multiplier < 1
        or config.maximum_observation_age_hours <= 0
    ):
        raise ValueError(f"Invalid forecast sufficiency settings: {path}")
    if config.early_warning_days < 0 or config.prediction_horizon_days <= 0:
        raise ValueError(f"Invalid forecast horizon settings: {path}")
    return config


def forecast_for_target(
    session: Session,
    target_id: UUID,
    settings: Settings,
    now: datetime,
) -> list[dict[str, object]]:
    start = naive_utc(now) - timedelta(days=settings.observation_retention_days)
    observations = list(session.scalars(select(Observation).where(
        Observation.target_id == target_id,
        Observation.timestamp >= start,
        Observation.timestamp <= naive_utc(now),
        Observation.metric_name.in_(("storage.tablespace.bytes_used", "storage.fra.space_used")),
    ).order_by(Observation.timestamp.desc(), Observation.id.desc()).limit(10000)))
    observations.sort(key=lambda observation: (observation.timestamp, observation.id))
    current = list(session.scalars(select(CurrentObservation).where(
        CurrentObservation.target_id == target_id,
        CurrentObservation.metric_name.in_(("storage.tablespace.bytes_total", "storage.fra.space_limit")),
    )))
    capacities: dict[str, float] = {}
    for current_observation in current:
        if current_observation.value is None:
            continue
        if current_observation.metric_name == "storage.tablespace.bytes_total":
            name = current_observation.labels.get(
                "tablespace_name", current_observation.labels.get("tablespace", "unknown"),
            )
            capacities[f"tablespace:{name}"] = current_observation.value
        else:
            capacities["fra"] = current_observation.value

    series: dict[str, list[ForecastPoint]] = {}
    for observation in observations:
        if observation.value is None:
            continue
        if observation.metric_name == "storage.tablespace.bytes_used":
            name = observation.labels.get(
                "tablespace_name", observation.labels.get("tablespace", "unknown"),
            )
            resource = f"tablespace:{name}"
        else:
            resource = "fra"
        series.setdefault(resource, []).append(ForecastPoint(observation.timestamp, observation.value))

    config = load_forecast_config()
    resources = set(series) | set(capacities)
    return [
        forecast_capacity(resource, series.get(resource, []), capacities.get(resource), config, naive_utc(now))
        for resource in sorted(resources)
    ]

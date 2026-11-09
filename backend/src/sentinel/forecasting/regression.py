"""Capacity forecasts from bounded historical observations; never extrapolate sparse data."""

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from sentinel.collectors.normalization import utcnow


@dataclass(frozen=True)
class ForecastConfig:
    minimum_points: int = 5
    minimum_span_hours: float = 24
    maximum_gap_multiplier: float = 3
    maximum_observation_age_hours: float = 72
    early_warning_days: float = 30
    prediction_horizon_days: float = 90


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: datetime
    used_bytes: float


def forecast_capacity(
    resource_id: str,
    points: list[ForecastPoint],
    capacity_bytes: float | None,
    config: ForecastConfig | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = now or utcnow()
    config = config or ForecastConfig()
    ordered = sorted(
        (point for point in points if math.isfinite(point.used_bytes) and point.used_bytes >= 0),
        key=lambda point: point.timestamp,
    )
    data_span_hours = (
        (ordered[-1].timestamp - ordered[0].timestamp).total_seconds() / 3600 if len(ordered) > 1 else 0
    )
    base: dict[str, object] = {
        "resource": resource_id,
        "status": "unknown",
        "capacity_bytes": capacity_bytes,
        "data_points": len(ordered),
        "data_span_hours": data_span_hours,
        "growth_rate_gb_per_day": None,
        "confidence_interval_gb_per_day": None,
        "days_to_exhaustion": None,
        "predicted_exhaustion_at": None,
        "early_warning": False,
        "prediction_horizon_days": config.prediction_horizon_days,
        "history": [{"timestamp": point.timestamp.isoformat(), "used_bytes": point.used_bytes} for point in ordered],
        "projection": [],
    }
    if (
        capacity_bytes is None or not math.isfinite(capacity_bytes) or capacity_bytes <= 0
        or len(ordered) < config.minimum_points or data_span_hours < config.minimum_span_hours
        or not _is_regular(ordered, config.maximum_gap_multiplier)
        or ordered[-1].timestamp > current_time
        or (current_time - ordered[-1].timestamp).total_seconds() > config.maximum_observation_age_hours * 3600
    ):
        return base

    origin = ordered[0].timestamp
    xs = [(point.timestamp - origin).total_seconds() / 86400 for point in ordered]
    ys = [point.used_bytes for point in ordered]
    x_mean, y_mean = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx <= 0:
        return base
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / sxx
    intercept = y_mean - slope * x_mean
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys, strict=True)]
    residual_error = math.sqrt(sum(residual * residual for residual in residuals) / max(1, len(xs) - 2))
    slope_error = residual_error / math.sqrt(sxx) if len(xs) > 2 else 0
    low_slope, high_slope = slope - 1.96 * slope_error, slope + 1.96 * slope_error
    base["status"] = "stable" if slope <= 0 else "forecast"
    base["growth_rate_gb_per_day"] = slope / (1024 ** 3)
    base["confidence_interval_gb_per_day"] = [
        low_slope / (1024 ** 3),
        high_slope / (1024 ** 3),
    ]
    current_used = max(0.0, ys[-1])
    exhaustion_days = max(0.0, (capacity_bytes - current_used) / slope) if slope > 0 else None
    if exhaustion_days is not None:
        base["days_to_exhaustion"] = exhaustion_days
        base["predicted_exhaustion_at"] = (current_time + timedelta(days=exhaustion_days)).isoformat()
        base["early_warning"] = exhaustion_days <= config.early_warning_days
    forecast_days = exhaustion_days if exhaustion_days is not None else config.prediction_horizon_days
    horizon = min(config.prediction_horizon_days, forecast_days)
    projection_days = sorted({0.0, horizon})
    base["projection"] = [
        {
            "timestamp": (current_time + timedelta(days=day)).isoformat(),
            "predicted_bytes": max(0.0, current_used + slope * day),
            "lower_bytes": min(capacity_bytes, max(0.0, current_used + low_slope * day)),
            "upper_bytes": min(capacity_bytes, max(0.0, current_used + high_slope * day)),
        }
        for day in projection_days
    ]
    return base


def _is_regular(points: list[ForecastPoint], maximum_gap_multiplier: float) -> bool:
    gaps = [
        (right.timestamp - left.timestamp).total_seconds()
        for left, right in pairwise(points)
    ]
    positive = [gap for gap in gaps if gap > 0]
    if len(positive) != len(gaps) or not positive:
        return False
    typical = statistics.median(positive)
    return max(positive) <= typical * maximum_gap_multiplier

from datetime import datetime, timedelta

from sentinel.forecasting.regression import ForecastConfig, ForecastPoint, forecast_capacity

GIB = 1024 ** 3


def _points(values):
    start = datetime(2026, 1, 1)
    return [ForecastPoint(start + timedelta(days=index), value) for index, value in enumerate(values)]


def test_insufficient_or_irregular_history_never_predicts_exhaustion():
    config = ForecastConfig(minimum_points=5, minimum_span_hours=24)
    insufficient = forecast_capacity("fra", _points([1, 2, 3]), 10, config, datetime(2026, 1, 3))
    assert insufficient["status"] == "unknown"
    assert insufficient["predicted_exhaustion_at"] is None
    irregular = [
        ForecastPoint(datetime(2026, 1, 1), 1),
        ForecastPoint(datetime(2026, 1, 1, 1), 2),
        ForecastPoint(datetime(2026, 1, 1, 2), 3),
        ForecastPoint(datetime(2026, 1, 1, 3), 4),
        ForecastPoint(datetime(2026, 1, 20), 5),
    ]
    assert forecast_capacity("fra", irregular, 10, config)["status"] == "unknown"


def test_linear_growth_produces_rate_confidence_and_early_warning():
    points = _points([1 * GIB, 3 * GIB, 5 * GIB, 7 * GIB, 9 * GIB])
    result = forecast_capacity(
        "fra", points, 10 * GIB, ForecastConfig(early_warning_days=2), datetime(2026, 1, 5),
    )
    assert result["status"] == "forecast"
    assert abs(result["growth_rate_gb_per_day"] - 2) < 1e-9
    assert result["days_to_exhaustion"] == 0.5
    assert result["early_warning"] is True
    assert len(result["confidence_interval_gb_per_day"]) == 2
    assert result["predicted_exhaustion_at"] is not None


def test_non_growing_history_is_stable_without_exhaustion_date():
    result = forecast_capacity(
        "tablespace:USERS", _points([5, 5, 5, 5, 5]), 10, now=datetime(2026, 1, 5),
    )
    assert result["status"] == "stable"
    assert result["predicted_exhaustion_at"] is None
    assert result["early_warning"] is False


def test_stale_history_returns_unknown_without_a_prediction():
    result = forecast_capacity(
        "fra",
        _points([1, 2, 3, 4, 5]),
        10,
        now=datetime(2026, 2, 1),
    )
    assert result["status"] == "unknown"
    assert result["predicted_exhaustion_at"] is None

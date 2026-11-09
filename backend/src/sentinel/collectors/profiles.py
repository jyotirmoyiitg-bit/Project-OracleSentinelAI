"""Validated per-target polling profiles."""

from typing import Any

from sentinel.collectors.normalization import PROBES
from sentinel.config.settings import Settings

PROFILE_MULTIPLIERS = {"critical": 0.5, "standard": 1.0, "lightweight": 2.0}


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if set(profile) - {"name", "intervals", "stale_after_seconds"}:
        raise ValueError("poll_profile supports name, intervals and stale_after_seconds only")
    name = profile.get("name", "standard")
    if not isinstance(name, str) or name not in PROFILE_MULTIPLIERS:
        raise ValueError("Unknown poll profile")
    for name in ("intervals", "stale_after_seconds"):
        values = profile.get(name, {})
        if not isinstance(values, dict) or set(values) - set(PROBES):
            raise ValueError(f"Invalid probe names in {name}")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 or v > 86400 for v in values.values()):
            raise ValueError(f"{name} values must be integer seconds between 1 and 86400")
    return profile


def interval_for(settings: Settings, profile: dict[str, Any], environment: str, probe: str) -> int:
    validate_profile(profile)
    name = profile.get("name", "critical" if environment == "production" else "standard")
    override = profile.get("intervals", {}).get(probe)
    if isinstance(override, int):
        return override
    return max(1, int(settings.poll_intervals[probe] * PROFILE_MULTIPLIERS[name]))


def stale_after(settings: Settings, profile: dict[str, Any], environment: str, probe: str) -> int:
    override = profile.get("stale_after_seconds", {}).get(probe)
    if isinstance(override, int):
        return override
    return max(settings.stale_after_seconds[probe], interval_for(settings, profile, environment, probe) * 3)

"""Read retained growth and the portal health tile. Oracle is not queried here."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from sentinel.config.settings import Settings
from sentinel.forecasting.service import forecast_for_target
from sentinel.persistence import session as database_session
from sentinel.persistence.models import DatabaseTarget

logger = logging.getLogger(__name__)


def growth_rows(settings: Settings, alias: str) -> tuple[list[dict[str, str]], str]:
    target_id, missing = _target_id(settings, alias)
    if target_id is None:
        return [], missing
    try:
        database_session.init_db(settings)
        factory = database_session.SessionLocal
        if factory is None:
            return [], "Tablespace growth could not be read from retained history."
        with factory() as session:
            forecasts = forecast_for_target(session, target_id, settings, datetime.now(UTC))
    except Exception:  # noqa: BLE001 -- connection text can contain a password
        logger.warning("tablespace growth read failed")
        return [], "Tablespace growth could not be read from retained history."
    rows = [
        {
            "source": "forecast",
            "resource": str(item.get("resource") or ""),
            "status": str(item.get("status") or ""),
            "growth_rate_gb_per_day": _text(item.get("growth_rate_gb_per_day")),
            "days_to_exhaustion": _text(item.get("days_to_exhaustion")),
        }
        for item in forecasts
    ]
    return rows, ""


def portal_row(settings: Settings, alias: str) -> dict[str, str] | None:
    try:
        database_session.init_db(settings)
        factory = database_session.SessionLocal
        if factory is None:
            return None
        with factory() as session:
            target = session.scalar(select(DatabaseTarget).where(DatabaseTarget.tns_alias == alias))
    except Exception:  # noqa: BLE001 -- connection text can contain a password
        logger.warning("portal status read failed")
        return None
    if target is None:
        return None
    return {
        "source": "portal",
        "display_name": target.display_name,
        "health": target.last_health_status or "unknown",
    }


def _target_id(settings: Settings, alias: str) -> tuple[UUID | None, str]:
    try:
        database_session.init_db(settings)
        factory = database_session.SessionLocal
        if factory is None:
            return None, "Tablespace growth could not be read from retained history."
        with factory() as session:
            target = session.scalar(select(DatabaseTarget).where(DatabaseTarget.tns_alias == alias))
    except Exception:  # noqa: BLE001 -- connection text can contain a password
        logger.warning("database alias lookup failed")
        return None, "Tablespace growth could not be read from retained history."
    if target is None:
        return None, "This alias is not registered, so retained growth cannot be read."
    return target.id, ""


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)

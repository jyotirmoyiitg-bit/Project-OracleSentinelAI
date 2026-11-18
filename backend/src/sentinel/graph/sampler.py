"""Timer that stores counter samples for GraphDB and WaitDB. It runs beside the collection scheduler."""

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings
from sentinel.graph.history import delete_expired, sample_fleet

logger = logging.getLogger(__name__)
_TICK_SECONDS = 30
_FIRST_SAMPLE_AFTER_SECONDS = 5


class GraphHistorySampler:
    """Checks every 30 seconds whether a sample is due, so a changed interval applies without a restart."""

    def __init__(self, settings: Settings, sessions: sessionmaker[Session]) -> None:
        self.settings = settings
        self.sessions = sessions
        self.last_run: datetime | None = None
        self.scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
        )
        self.scheduler.add_job(
            self.tick, "interval", seconds=_TICK_SECONDS, id="graph-history-tick", replace_existing=True,
            next_run_time=datetime.now().astimezone() + timedelta(seconds=_FIRST_SAMPLE_AFTER_SECONDS),
        )
        self.scheduler.add_job(self.retain, "interval", hours=1, id="graph-history-retention", replace_existing=True)

    def due(self, now: datetime) -> bool:
        if not self.settings.graph_history_enabled:
            return False
        if self.last_run is None:
            return True
        # A tick can land a moment early; half a tick of slack keeps the configured cadence.
        return (now - self.last_run).total_seconds() >= self.settings.graph_history_interval_seconds - _TICK_SECONDS / 2

    def tick(self) -> int:
        now = utcnow()
        if not self.due(now):
            return 0
        self.last_run = now
        try:
            stored = asyncio.run(sample_fleet(self.settings, self.sessions))
        except Exception as exc:  # noqa: BLE001 -- a failed sweep must not stop the timer
            logger.warning("graph history sweep failed: %s", type(exc).__name__)
            return 0
        logger.info("graph history stored %s samples", stored)
        return stored

    def retain(self) -> None:
        try:
            removed = delete_expired(self.settings, self.sessions)
            if removed:
                logger.info("graph history removed %s samples", removed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("graph history retention failed: %s", type(exc).__name__)

    def start(self) -> None:
        self.scheduler.start()
        logger.info("Graph history sampler started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Graph history sampler stopped")

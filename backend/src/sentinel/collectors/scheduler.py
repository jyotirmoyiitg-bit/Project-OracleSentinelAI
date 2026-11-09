"""One process-local scheduler, with database leases fencing multi-process overlap."""

import logging
from datetime import timedelta

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from sentinel.collectors.alert_flow import ALERT_COLLECTOR_TYPE, AlertEvaluationRunner
from sentinel.collectors.checkpoints import get_checkpoint
from sentinel.collectors.jobs import CollectionRunner
from sentinel.collectors.normalization import PROBES, utcnow
from sentinel.collectors.profiles import interval_for
from sentinel.persistence.models import DatabaseTarget

logger = logging.getLogger(__name__)


class CollectorScheduler:
    def __init__(self, runner: CollectionRunner, alert_runner: AlertEvaluationRunner | None = None):
        self.runner = runner
        self.alert_runner = alert_runner
        self.scheduler = BackgroundScheduler(
            timezone="UTC", executors={"default": ThreadPoolExecutor(runner.settings.scheduler_workers)},
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
        )

    def reconcile(self) -> None:
        desired = set()
        with self.runner.sessions() as session:
            targets = session.scalars(select(DatabaseTarget).where(DatabaseTarget.enabled.is_(True))).all()
            for target in targets:
                for probe in PROBES:
                    job_id = f"poll:{target.id}:{probe}"
                    desired.add(job_id)
                    seconds = interval_for(self.runner.settings, target.poll_profile, target.environment, probe)
                    existing = self.scheduler.get_job(job_id)
                    if existing and existing.trigger.interval.total_seconds() == seconds:
                        continue
                    checkpoint = get_checkpoint(session, target.id, probe)
                    previous = checkpoint.last_success_at if checkpoint else None
                    due = max(utcnow(), previous + timedelta(seconds=seconds)) if previous else utcnow()
                    self.scheduler.add_job(
                        self.runner.run, "interval", seconds=seconds, id=job_id, args=[target.id, probe],
                        replace_existing=True, next_run_time=due,
                    )
                if self.alert_runner is not None:
                    alert_job_id = f"alert:{target.id}"
                    desired.add(alert_job_id)
                    seconds = self.runner.settings.alert_poll_interval_seconds
                    existing = self.scheduler.get_job(alert_job_id)
                    if existing and existing.trigger.interval.total_seconds() == seconds:
                        continue
                    checkpoint = get_checkpoint(session, target.id, ALERT_COLLECTOR_TYPE)
                    previous = checkpoint.last_success_at if checkpoint else None
                    due = max(utcnow(), previous + timedelta(seconds=seconds)) if previous else utcnow()
                    self.scheduler.add_job(
                        self.alert_runner.run, "interval", seconds=seconds, id=alert_job_id, args=[target.id],
                        replace_existing=True, next_run_time=due,
                    )
        for job in self.scheduler.get_jobs():
            if job.id.startswith(("poll:", "alert:")) and job.id not in desired:
                self.scheduler.remove_job(job.id)

    def start(self) -> None:
        self.reconcile()
        self.scheduler.add_job(
            self.reconcile, "interval", seconds=self.runner.settings.scheduler_reconcile_seconds,
            id="reconcile", replace_existing=True,
        )
        self.scheduler.add_job(self.runner.retain, "interval", hours=1, id="retention", replace_existing=True)
        self.scheduler.start()
        logger.info("Collector scheduler started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("Collector scheduler stopped")

"""Background work for agents: automatic investigation of new critical incidents, and snapshot retention."""

import asyncio
import logging
import uuid
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sentinel.agents.collector import delete_expired
from sentinel.agents.config import effective
from sentinel.agents.runner import AgentRunner, RunRefused
from sentinel.ai.gateway import build_gateway
from sentinel.collectors.normalization import utcnow
from sentinel.config.secrets import SecretResolver
from sentinel.config.settings import Settings
from sentinel.knowledge.acl import Caller
from sentinel.persistence.models import AgentRun, DatabaseTarget, Incident

logger = logging.getLogger(__name__)
# Automatic runs act as an operator, so they never read admin-only rows and their findings are open to every role.
SYSTEM_CALLER = Caller("operator", "agent")
_PER_SWEEP = 2


def pending_incidents(session: Session, settings: Settings) -> list[uuid.UUID]:
    """New critical incidents from the last day that have no run yet, on databases that allow automatic runs."""
    if not settings.agents_enabled:
        return []
    rows = session.scalars(
        select(Incident).where(
            Incident.severity == "critical", Incident.state == "new",
            Incident.first_seen >= utcnow() - timedelta(hours=24),
        ).order_by(Incident.first_seen.desc()).limit(50),
    ).all()
    picked: list[uuid.UUID] = []
    for incident in rows:
        if session.scalar(select(AgentRun.id).where(AgentRun.incident_id == incident.id).limit(1)) is not None:
            continue
        target = session.get(DatabaseTarget, incident.target_id)
        if target is None or not target.enabled:
            continue
        applied = effective(session, settings, target.id)
        if applied.enabled and applied.auto_investigate:
            picked.append(incident.id)
        if len(picked) >= _PER_SWEEP:
            break
    return picked


async def investigate_pending(settings: Settings, sessions: sessionmaker[Session], runner: AgentRunner) -> int:
    """Start and finish an investigation for each pending incident. Returns how many ran."""
    with sessions() as session:
        incident_ids = pending_incidents(session, settings)
    ran = 0
    for incident_id in incident_ids:
        try:
            run_id = runner.start_investigation(SYSTEM_CALLER, incident_id=incident_id, trigger="incident")
        except RunRefused:
            continue
        await runner.run(run_id)
        ran += 1
    return ran


class AgentScheduler:
    """Timer for the incident sweep and the retention sweep. Independent of Oracle collection."""

    def __init__(self, settings: Settings, sessions: sessionmaker[Session]) -> None:
        self.settings = settings
        self.sessions = sessions
        self.scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
        )
        self.scheduler.add_job(
            self.sweep, "interval", seconds=settings.agent_sweep_seconds, id="agent-incident-sweep", replace_existing=True,
        )
        self.scheduler.add_job(self.retain, "interval", hours=1, id="agent-retention", replace_existing=True)

    def sweep(self) -> None:
        if not self.settings.agents_enabled:
            return
        try:
            gateway = build_gateway(self.settings, SecretResolver(self.settings.secret_key))
            runner = AgentRunner(self.settings, self.sessions, gateway)
            asyncio.run(investigate_pending(self.settings, self.sessions, runner))
        except Exception as exc:  # noqa: BLE001 -- a failed sweep must not stop the timer
            logger.warning("agent incident sweep failed: %s", type(exc).__name__)

    def retain(self) -> None:
        try:
            removed = delete_expired(self.settings, self.sessions)
            if removed:
                logger.info("agent retention removed %s snapshots", removed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent retention failed: %s", type(exc).__name__)

    def start(self) -> None:
        self.scheduler.start()
        logger.info("Agent scheduler started")

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Agent scheduler stopped")

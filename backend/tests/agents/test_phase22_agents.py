"""Phase 22: snapshots, the Collector, the Investigator, the Verifier, and per-database agent settings."""

import asyncio
import importlib
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sentinel.agents import collector as collector_module
from sentinel.agents.collector import CollectionRefused, collect_snapshot, delete_expired
from sentinel.agents.compare import row_changes
from sentinel.agents.config import SettingError, describe, effective, set_override
from sentinel.agents.packs import ALL_KINDS, PACKS, RESTRICTED_KINDS, SENSITIVE_KINDS, kinds_for, parse_packs
from sentinel.agents.playbooks import match_playbook
from sentinel.agents.runner import AgentRunner, RunRefused
from sentinel.agents.scheduler import SYSTEM_CALLER, investigate_pending, pending_incidents
from sentinel.agents.verifier import verify
from sentinel.ai.activity import TOOL_NAMES, ActivityReading
from sentinel.ai.gateway import ChatGateway
from sentinel.ai.types import ModelReply
from sentinel.collectors.normalization import utcnow
from sentinel.knowledge.acl import Caller
from sentinel.mcp.server import run_tool
from sentinel.persistence.models import (
    AgentFinding,
    AgentRun,
    AgentStep,
    AuditEvent,
    EvidenceSnapshot,
    Incident,
)

VIEWER = {"X-Sentinel-Role": "viewer", "X-Sentinel-Principal": "vera"}
OPERATOR = {"X-Sentinel-Role": "operator", "X-Sentinel-Principal": "olga"}
ADMIN = {"X-Sentinel-Role": "admin", "X-Sentinel-Principal": "sentinel"}
OLGA = Caller("operator", "olga")
ROOT = Caller("admin", "sentinel")


@pytest.fixture
def on(settings):
    """Agents on, with no gap between collections so a test can collect twice."""
    return settings.model_copy(update={"agents_enabled": True, "agent_snapshot_min_gap_seconds": 0})


def _incident(sessions, target_id, message="ORA-00257: archiver error. Connect internal only, until freed.", **extra) -> uuid.UUID:
    with sessions.begin() as session:
        values = {
            "fingerprint": str(uuid.uuid4()), "target_id": target_id, "rule_id": "alert.ora", "state": "new",
            "severity": "critical", "message": message,
        }
        values.update(extra)
        incident = Incident(**values)
        session.add(incident)
        session.flush()
        return incident.id


class _Model:
    """Stand-in local model. Returns a plan when asked for a playbook and a finding otherwise."""

    name = "local"
    model = "stub"

    def __init__(self, finding: dict[str, object] | list[dict[str, object]], playbook: str = "general") -> None:
        self.findings = finding if isinstance(finding, list) else [finding]
        self.playbook = playbook
        self.plans = 0
        self.prompts: list[str] = []

    async def generate(self, messages, *, tools, structured_schema, correlation_id):
        del correlation_id
        assert tools is None
        if "playbook" in structured_schema.get("properties", {}):
            self.plans += 1
            return ModelReply(json.dumps({"playbook": self.playbook}), 3, 2, self.name, self.model)
        self.prompts.append(messages[-1]["content"])
        reply = self.findings[min(len(self.prompts) - 1, len(self.findings) - 1)]
        return ModelReply(json.dumps(reply), 40, 20, self.name, self.model)

    async def health(self) -> dict[str, object]:
        return {"status": "ok"}


def _gateway(model: _Model) -> ChatGateway:
    return ChatGateway(model, "disclosure", True)


def _investigate(sessions, settings, incident_id, caller=OLGA, gateway=None) -> uuid.UUID:
    runner = AgentRunner(settings, sessions, gateway)
    run_id = runner.start_investigation(caller, incident_id=incident_id)
    asyncio.run(runner.run(run_id))
    return run_id


def _steps(sessions, run_id) -> list[AgentStep]:
    with sessions() as session:
        return list(session.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq)))


def _finding(sessions, run_id) -> AgentFinding:
    with sessions() as session:
        return session.scalars(select(AgentFinding).where(AgentFinding.run_id == run_id)).one()


def _client(settings, sessions) -> TestClient:
    with patch("sentinel.config.settings.get_settings", return_value=settings):
        main = importlib.import_module("sentinel.main")
    app = main.create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.session_factory = sessions
    return client


# ---- packs and settings


def test_every_snapshot_reading_is_an_allowlisted_fixed_reading() -> None:
    assert set(ALL_KINDS) <= set(TOOL_NAMES)
    assert set(PACKS) == {"core", "performance", "configuration"}
    assert set(PACKS["configuration"]) <= RESTRICTED_KINDS
    assert {"sessions", "top_sql", "database_users"} <= SENSITIVE_KINDS <= RESTRICTED_KINDS
    assert "tablespace" not in RESTRICTED_KINDS


def test_sensitive_readings_are_left_out_when_a_database_does_not_store_them() -> None:
    assert "sessions" in kinds_for(("core",), True)
    without = kinds_for(("core", "performance"), False)
    assert not set(without) & SENSITIVE_KINDS
    assert "tablespace" in without
    assert parse_packs("Core, performance") == ("core", "performance")
    with pytest.raises(ValueError, match="core, performance"):
        parse_packs("core,everything")
    with pytest.raises(ValueError, match="core, performance"):
        parse_packs([])


def test_a_database_value_wins_and_clearing_it_restores_the_system_default(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    with sessions.begin() as session:
        assert effective(session, on, target_id).retention_days == 14
        assert set_override(session, on, target_id, "agent_snapshot_retention_days", 3, "sentinel") == 3
        set_override(session, on, target_id, "agent_snapshot_packs", "core", "sentinel")
        set_override(session, on, target_id, "agent_auto_investigate", True, "sentinel")
        applied = effective(session, on, target_id)
        assert (applied.retention_days, applied.packs, applied.auto_investigate) == (3, ("core",), True)
        row = next(item for item in describe(session, on, target_id) if item["key"] == "agent_snapshot_retention_days")
        assert row["overridden"] is True
        assert (row["system_value"], row["database_value"], row["value"]) == (14, 3, 3)
        set_override(session, on, target_id, "agent_snapshot_retention_days", None, "sentinel")
        assert effective(session, on, target_id).retention_days == 14


@pytest.mark.parametrize(("key", "value"), [
    ("agent_snapshot_retention_days", 45),
    ("agent_snapshot_retention_days", "3"),
    ("agent_snapshot_packs", "core,everything"),
    ("agent_collect_sensitive", "yes"),
    ("agent_max_steps", 4),
    ("secret_key", "x"),
])
def test_an_invalid_database_value_is_refused(on, sessions, make_target, key, value) -> None:
    target_id = make_target(alias="FINANCE")
    with sessions.begin() as session, pytest.raises(SettingError):
        set_override(session, on, target_id, key, value, "sentinel")


def test_the_system_switch_is_the_master_and_a_database_can_only_opt_out(settings, on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    with sessions.begin() as session:
        set_override(session, on, target_id, "agents_enabled", True, "sentinel")
        assert effective(session, settings, target_id).enabled is False
        assert effective(session, on, target_id).enabled is True
        set_override(session, on, target_id, "agents_enabled", False, "sentinel")
        assert effective(session, on, target_id).enabled is False


# ---- the collector


def test_a_snapshot_stores_one_row_per_reading_and_flags_restricted_ones(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    batch = asyncio.run(collect_snapshot(on, sessions, target_id))
    assert [item.kind for item in batch.items] == kinds_for(("core", "performance"), True)
    assert batch.answered == len(batch.items)
    with sessions() as session:
        rows = session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.target_id == target_id)).all()
        assert {row.batch_id for row in rows} == {batch.batch_id}
        by_kind = {row.kind: row for row in rows}
        assert by_kind["tablespace"].restricted is False
        assert by_kind["tablespace"].row_count == 3
        assert by_kind["tablespace"].rows[0]["tablespace_name"]
        assert by_kind["sessions"].restricted is True
        assert by_kind["top_sql"].pack == "performance"
        assert len(by_kind["tablespace"].digest) == 64


def test_a_collection_is_refused_when_agents_are_off_too_recent_or_the_database_is_disabled(
    settings, on, sessions, make_target,
) -> None:
    target_id = make_target(alias="FINANCE")
    disabled_id = make_target(alias="RETIRED", enabled=False)
    with pytest.raises(CollectionRefused) as off:
        asyncio.run(collect_snapshot(settings, sessions, target_id))
    assert off.value.code == "agents_off"
    with pytest.raises(CollectionRefused) as disabled:
        asyncio.run(collect_snapshot(on, sessions, disabled_id))
    assert disabled.value.code == "disabled"
    gapped = on.model_copy(update={"agent_snapshot_min_gap_seconds": 600})
    asyncio.run(collect_snapshot(gapped, sessions, target_id))
    with pytest.raises(CollectionRefused) as soon:
        asyncio.run(collect_snapshot(gapped, sessions, target_id))
    assert soon.value.code == "too_soon"
    with sessions() as session:
        assert len({row.batch_id for row in session.scalars(select(EvidenceSnapshot))}) == 1


def test_a_database_that_does_not_store_sensitive_rows_never_reads_them(on, sessions, make_target, monkeypatch) -> None:
    target_id = make_target(alias="FINANCE")
    asked: list[str] = []

    async def read(_settings, _alias, kinds):
        asked.extend(kinds)
        return {kind: ActivityReading(kind, TOOL_NAMES[kind], "ok", [{"source": "test"}], "") for kind in kinds}

    monkeypatch.setattr(collector_module, "read_kinds", read)
    with sessions.begin() as session:
        set_override(session, on, target_id, "agent_collect_sensitive", False, "sentinel")
    batch = asyncio.run(collect_snapshot(on, sessions, target_id))
    assert asked
    assert not set(asked) & SENSITIVE_KINDS
    assert not {item.kind for item in batch.items} & SENSITIVE_KINDS


def test_an_unreadable_reading_is_stored_as_unread_and_does_not_fail_the_others(on, sessions, make_target, monkeypatch) -> None:
    target_id = make_target(alias="FINANCE")

    async def read(_settings, _alias, kinds):
        return {
            kind: ActivityReading(kind, TOOL_NAMES[kind], "ok", [{"source": "test", "name": kind}], "")
            if kind != "backups" else ActivityReading(kind, TOOL_NAMES[kind], "not_granted", [], "Grant SELECT on V$RMAN_STATUS.")
            for kind in kinds
        }

    monkeypatch.setattr(collector_module, "read_kinds", read)
    batch = asyncio.run(collect_snapshot(on, sessions, target_id, packs=("core",)))
    statuses = {item.kind: item.status for item in batch.items}
    assert statuses["backups"] == "not_granted"
    assert batch.answered == len(batch.items) - 1


def test_snapshots_older_than_the_retention_are_deleted_per_database(on, sessions, make_target) -> None:
    kept_id = make_target(alias="KEEPS")
    short_id = make_target(alias="SHORT")
    with sessions.begin() as session:
        set_override(session, on, short_id, "agent_snapshot_retention_days", 2, "sentinel")
        for target_id in (kept_id, short_id):
            for days in (1, 5, 20):
                session.add(EvidenceSnapshot(
                    batch_id=uuid.uuid4(), target_id=target_id, kind="tablespace", pack="core",
                    taken_at=utcnow() - timedelta(days=days), rows=[],
                ))
    assert delete_expired(on, sessions) == 3
    with sessions() as session:
        ages = {
            (row.target_id, (utcnow() - row.taken_at).days) for row in session.scalars(select(EvidenceSnapshot))
        }
    assert ages == {(kept_id, 1), (kept_id, 5), (short_id, 1)}


def test_the_readings_server_runs_a_snapshot_only_from_listed_readings(monkeypatch) -> None:
    monkeypatch.setenv("SENTINEL_MCP_SYNTHETIC", "1")
    payload = run_tool("collect_snapshot", {"alias": "PDBORCL", "readings": ["get_redo_logs", "get_sessions"]})
    assert set(payload["readings"]) == {"get_redo_logs", "get_sessions"}
    assert payload["readings"]["get_redo_logs"]["status"] == "ok"
    for arguments in (
        {"alias": "PDBORCL", "readings": ["drop_everything"]},
        {"alias": "PDBORCL", "readings": ["get_sessions"], "sql": "select 1 from dual"},
        {"alias": "PDBORCL", "readings": []},
        {"alias": "bad alias;", "readings": ["get_sessions"]},
    ):
        with pytest.raises(ValueError, match=r"reading|alias|accepted"):
            run_tool("collect_snapshot", arguments)


# ---- comparing


def test_changes_name_what_moved_and_ignore_small_or_rotating_values() -> None:
    before = [
        {"source": "dba_tablespaces", "tablespace_name": "USERS", "used_pct": "71", "bytes_used": "7100"},
        {"source": "dba_tablespaces", "tablespace_name": "SYSTEM", "used_pct": "40", "bytes_used": "4000"},
    ]
    after = [
        {"source": "dba_tablespaces", "tablespace_name": "USERS", "used_pct": "97", "bytes_used": "9700"},
        {"source": "dba_tablespaces", "tablespace_name": "SYSTEM", "used_pct": "41", "bytes_used": "4010"},
        {"source": "dba_tablespaces", "tablespace_name": "AUDIT_TS", "used_pct": "5", "bytes_used": "50"},
    ]
    lines = row_changes("tablespace", before, after)
    assert "New: AUDIT_TS" in lines
    assert "USERS used_pct went from 71 to 97" in lines
    assert not any("SYSTEM" in line for line in lines)
    redo_before = [{"source": "v$log", "group_no": "1", "status": "CURRENT", "bytes": "200"}]
    redo_after = [{"source": "v$log", "group_no": "1", "status": "INACTIVE", "bytes": "200"}]
    assert row_changes("redo_logs", redo_before, redo_after) == []
    errors = row_changes("ora_errors", [], [{"source": "alert", "occurred_at": "10:00", "message_text": "ORA-00257"}])
    assert errors[0].startswith("1 new:")
    assert row_changes("tablespace", before, before) == []


# ---- verifier and playbooks


def test_a_conclusion_passes_only_when_the_evidence_supports_it() -> None:
    evidence = [
        "FINANCE: ORA-00257: archiver error",
        "FRA storage.fra.used_pct is 97.4 now and was 71 about a day ago",
        "Recovery: last successful backup 2026-11-12 02:00",
    ]
    others = ["WAREHOUSE-DEV", "REPORTING"]
    ok, note = verify(
        ["The recovery area is 97 percent used, up from 71, so ORA-00257 stopped the archiver.", "Back up archived logs."],
        evidence, "FINANCE", others,
    )
    assert ok, note
    assert verify(["Use is 97.4 percent after 2 or 3 days without a backup."], evidence, "FINANCE", others)[0]
    cases = {
        "The recovery area is 85 percent used.": "figure 85",
        "ORA-04031 shows the shared pool is exhausted.": "ORA-04031",
        "Run alter system set db_recovery_file_dest_size=200G to fix it.": "statement or command",
        "Run SELECT name FROM v$database first.": "statement or command",
        "WAREHOUSE-DEV has the same problem.": "another database",
        "": "empty",
    }
    for text, reason in cases.items():
        passed, note = verify([text], evidence, "FINANCE", others)
        assert not passed, text
        assert reason in note


@pytest.mark.parametrize(("text", "name"), [
    ("alert.ora: ORA-00257: archiver error", "archive_destination"),
    ("fra.used: recovery area is 96 percent used", "archive_destination"),
    ("ORA-01653: unable to extend table APP.ORDERS by 128 in tablespace USERS", "tablespace_full"),
    ("ORA-00060: deadlock detected while waiting for resource", "blocking_sessions"),
    ("ORA-00020: maximum number of processes (300) exceeded", "session_limit"),
    ("ORA-04031: unable to allocate 4096 bytes of shared memory", "memory_pressure"),
    ("connectivity: database unreachable, ORA-12541", "unavailable"),
    ("ORA-19502: write error on file, RMAN backup failed", "backup_failure"),
    ("ORA-12012: error on auto execute of job", "job_failure"),
])
def test_an_incident_text_reaches_its_playbook(text: str, name: str) -> None:
    playbook = match_playbook(text)
    assert playbook is not None
    assert playbook.name == name


def test_a_text_with_no_known_pattern_matches_no_playbook() -> None:
    assert match_playbook("Something odd happened to the reports last night") is None


# ---- the investigator


def test_an_investigation_without_a_model_saves_every_step_and_an_evidence_finding(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    incident_id = _incident(sessions, target_id)
    run_id = _investigate(sessions, on, incident_id)
    steps = _steps(sessions, run_id)
    assert [step.kind for step in steps] == ["plan", "collect", "compare", "lookup", "verify", "finding"]
    assert steps[0].title == "Plan: Archive destination or recovery area full"
    assert "matched by a fixed rule" in steps[0].detail["lines"][0]
    assert steps[1].title == "Collect: 5 of 5 readings"
    assert steps[2].detail["lines"][0].startswith("No earlier snapshot")
    with sessions() as session:
        run = session.get(AgentRun, run_id)
        assert (run.status, run.playbook, run.llm_used, run.model_calls) == ("completed", "archive_destination", False, 0)
        assert run.incident_id == incident_id
        assert run.finished_at is not None
        audit = session.scalars(select(AuditEvent).where(AuditEvent.action == "agent.run")).one()
        assert (audit.principal, audit.outcome, audit.target) == ("olga", "completed", str(run_id))
        assert {row.run_id for row in session.scalars(select(EvidenceSnapshot))} == {run_id}
    finding = _finding(sessions, run_id)
    assert finding.written_by == "rules"
    assert finding.verified is True
    assert finding.confidence == "none"
    assert finding.severity == "critical"
    assert finding.summary.startswith("The language model is off, so no cause was written.")
    assert finding.next_steps
    assert finding.incident_id == incident_id
    assert finding.restricted is False


def test_a_second_investigation_reports_what_changed_since_the_first(on, sessions, make_target, monkeypatch) -> None:
    target_id = make_target(alias="FINANCE")
    used = {"value": "71"}

    async def read(_settings, _alias, kinds):
        return {
            kind: ActivityReading(kind, TOOL_NAMES[kind], "ok", [
                {"source": "dba_tablespaces", "tablespace_name": "USERS", "used_pct": used["value"]},
            ], "")
            for kind in kinds
        }

    monkeypatch.setattr(collector_module, "read_kinds", read)
    first = _investigate(sessions, on, _incident(sessions, target_id, "ORA-01653: unable to extend in tablespace USERS"))
    used["value"] = "97"
    with sessions.begin() as session:
        for row in session.scalars(select(EvidenceSnapshot)):
            row.taken_at = row.taken_at - timedelta(hours=3)
    second = _investigate(sessions, on, _incident(sessions, target_id, "ORA-01653: unable to extend in tablespace USERS"))
    assert first != second
    compare = next(step for step in _steps(sessions, second) if step.kind == "compare")
    assert any("USERS used_pct went from 71 to 97" in line for line in compare.detail["lines"])
    assert "USERS used_pct went from 71 to 97" in _finding(sessions, second).summary


def test_an_operator_run_leaves_restricted_readings_out_and_an_admin_run_is_marked_restricted(
    on, sessions, make_target,
) -> None:
    target_id = make_target(alias="FINANCE")
    message = "ORA-00060: deadlock detected while waiting for resource"
    operator_run = _investigate(sessions, on, _incident(sessions, target_id, message))
    collect = next(step for step in _steps(sessions, operator_run) if step.kind == "collect")
    assert collect.title == "Collect: 2 of 2 readings"
    assert "Left out for your role: Blocking sessions, Sessions." in collect.detail["lines"]
    assert _finding(sessions, operator_run).restricted is False
    with sessions() as session:
        kinds = {row.kind for row in session.scalars(select(EvidenceSnapshot))}
    assert not kinds & RESTRICTED_KINDS

    model = _Model({"cause": "A session is blocked on FINANCE.", "confidence": "medium", "evidence": [2, 3, 4, 5, 6, 7, 8, 9]})
    admin_run = _investigate(sessions, on, _incident(sessions, target_id, message), caller=ROOT, gateway=_gateway(model))
    collect = next(step for step in _steps(sessions, admin_run) if step.kind == "collect")
    assert collect.title == "Collect: 4 of 4 readings"
    finding = _finding(sessions, admin_run)
    assert finding.written_by == "model"
    assert finding.restricted is True


def test_the_model_writes_the_cause_and_the_verifier_passes_it(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    make_target(alias="WAREHOUSE-DEV")
    model = _Model({
        "cause": "ORA-00257 on FINANCE shows the archiver cannot write.", "confidence": "medium",
        "evidence": [1, 2], "next_steps": ["Back up and remove archived logs."], "need": [],
    })
    run_id = _investigate(sessions, on, _incident(sessions, target_id), gateway=_gateway(model))
    steps = _steps(sessions, run_id)
    assert [step.kind for step in steps] == ["plan", "collect", "compare", "lookup", "conclude", "verify", "finding"]
    assert model.plans == 0
    assert len(model.prompts) == 1
    assert "[1] FINANCE: alert.ora: ORA-00257" in model.prompts[0]
    assert "Readings you may ask for:" in model.prompts[0]
    assert "get_sessions" not in model.prompts[0].split("Evidence:")[0]
    finding = _finding(sessions, run_id)
    assert (finding.written_by, finding.verified, finding.confidence) == ("model", True, "medium")
    assert finding.summary == "ORA-00257 on FINANCE shows the archiver cannot write."
    assert finding.next_steps == ["Back up and remove archived logs."]
    assert finding.evidence[0]["ref"] == "goal"
    with sessions() as session:
        run = session.get(AgentRun, run_id)
        assert (run.llm_used, run.model_calls, run.model) == (True, 1, "stub")
        conclude = next(step for step in steps if step.kind == "conclude")
        assert (conclude.prompt_tokens, conclude.completion_tokens) == (40, 20)


@pytest.mark.parametrize("cause", [
    "The recovery area is 99 percent used on FINANCE.",
    "ORA-04031 shows the shared pool is exhausted.",
    "WAREHOUSE-DEV filled the shared archive destination.",
    "Run ALTER SYSTEM SET log_archive_dest_1='LOCATION=/u02/arch' to fix it.",
])
def test_a_conclusion_the_evidence_does_not_support_is_shown_as_evidence_only(on, sessions, make_target, cause) -> None:
    target_id = make_target(alias="FINANCE")
    make_target(alias="WAREHOUSE-DEV")
    model = _Model({"cause": cause, "confidence": "high", "evidence": [1]})
    run_id = _investigate(sessions, on, _incident(sessions, target_id), gateway=_gateway(model))
    verify_step = next(step for step in _steps(sessions, run_id) if step.kind == "verify")
    assert verify_step.outcome == "failed"
    finding = _finding(sessions, run_id)
    assert (finding.written_by, finding.verified, finding.confidence) == ("rules", False, "none")
    assert cause not in finding.summary
    assert finding.summary.startswith("The model's conclusion did not match the evidence")


def test_the_model_may_ask_once_for_more_evidence_from_the_allowlist(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    model = _Model([
        {"cause": "More evidence is needed.", "confidence": "low", "evidence": [1],
         "need": ["get_tablespace_pressure", "list_database_users", "drop_table"]},
        {"cause": "ORA-00257 on FINANCE shows the archiver cannot write.", "confidence": "medium", "evidence": [1], "need": ["get_memory_status"]},
    ])
    run_id = _investigate(sessions, on, _incident(sessions, target_id), gateway=_gateway(model))
    steps = _steps(sessions, run_id)
    assert [step.kind for step in steps] == [
        "plan", "collect", "compare", "lookup", "conclude", "collect", "conclude", "verify", "finding",
    ]
    assert steps[5].title == "Collect more evidence: 1 of 1 readings"
    assert steps[5].detail["lines"][0].startswith("Tablespaces:")
    assert len(model.prompts) == 2
    assert "Tablespaces:" in model.prompts[1]
    with sessions() as session:
        kinds = {row.kind for row in session.scalars(select(EvidenceSnapshot))}
    assert "tablespace" in kinds
    assert "database_users" not in kinds
    assert "memory" not in kinds


def test_the_model_chooses_a_playbook_only_when_no_fixed_rule_matches(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    finding = {"cause": "The evidence does not show a cause.", "confidence": "low", "evidence": [1]}
    model = _Model(finding, playbook="memory_pressure")
    run_id = _investigate(
        sessions, on, _incident(sessions, target_id, "Reports were slow and users complained"), gateway=_gateway(model),
    )
    plan = _steps(sessions, run_id)[0]
    assert model.plans == 1
    assert plan.title == "Plan: Memory pressure"
    assert "chosen by the model" in plan.detail["lines"][0]
    invented = _Model(finding, playbook="reboot_everything")
    run_id = _investigate(
        sessions, on, _incident(sessions, target_id, "Reports were slow and users complained"), gateway=_gateway(invented),
    )
    assert _steps(sessions, run_id)[0].title == "Plan: General health check"


def test_only_the_local_model_is_used_and_the_model_call_budget_is_kept(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    hosted = _Model({"cause": "ORA-00257 on FINANCE.", "confidence": "low", "evidence": [1]})
    hosted.name = "hosted"
    run_id = _investigate(sessions, on, _incident(sessions, target_id), gateway=_gateway(hosted))
    assert hosted.prompts == []
    assert _finding(sessions, run_id).written_by == "rules"
    capped = on.model_copy(update={"agent_max_model_calls": 0})
    local = _Model({"cause": "ORA-00257 on FINANCE.", "confidence": "low", "evidence": [1]})
    run_id = _investigate(sessions, capped, _incident(sessions, target_id), gateway=_gateway(local))
    assert local.prompts == []
    with sessions() as session:
        assert session.get(AgentRun, run_id).model_calls == 0


def test_a_run_that_passes_its_time_limit_is_stopped_with_its_steps_kept(on, sessions, make_target, monkeypatch) -> None:
    target_id = make_target(alias="FINANCE")

    async def slow(_settings, _alias, kinds):
        await asyncio.sleep(5)
        return {}

    monkeypatch.setattr(collector_module, "read_kinds", slow)
    runner = AgentRunner(on, sessions, None)
    run_id = runner.start_investigation(OLGA, incident_id=_incident(sessions, target_id))
    asyncio.run(runner.run(run_id, timeout_seconds=0.2))
    with sessions() as session:
        run = session.get(AgentRun, run_id)
        assert run.status == "stopped"
        assert "time limit" in run.note
    assert [step.kind for step in _steps(sessions, run_id)] == ["plan"]


def test_an_investigation_is_refused_when_agents_are_off_or_one_is_already_running(settings, on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    incident_id = _incident(sessions, target_id)
    with pytest.raises(RunRefused, match="turned off"):
        AgentRunner(settings, sessions, None).start_investigation(OLGA, incident_id=incident_id)
    runner = AgentRunner(on, sessions, None)
    runner.start_investigation(OLGA, incident_id=incident_id)
    with pytest.raises(RunRefused, match="already running"):
        runner.start_investigation(OLGA, incident_id=incident_id)
    with pytest.raises(RunRefused, match="not found"):
        runner.start_investigation(OLGA, incident_id=uuid.uuid4())


# ---- automatic investigation


def test_a_new_critical_incident_is_investigated_once_when_the_database_allows_it(on, sessions, make_target) -> None:
    auto_id = make_target(alias="FINANCE")
    manual_id = make_target(alias="WAREHOUSE")
    with sessions.begin() as session:
        set_override(session, on, auto_id, "agent_auto_investigate", True, "sentinel")
    critical = _incident(sessions, auto_id)
    _incident(sessions, auto_id, severity="warning")
    _incident(sessions, auto_id, state="acknowledged")
    _incident(sessions, manual_id)
    with sessions() as session:
        assert pending_incidents(session, on) == [critical]
        assert pending_incidents(session, on.model_copy(update={"agents_enabled": False})) == []
    runner = AgentRunner(on, sessions, None)
    assert asyncio.run(investigate_pending(on, sessions, runner)) == 1
    assert asyncio.run(investigate_pending(on, sessions, runner)) == 0
    with sessions() as session:
        run = session.scalars(select(AgentRun)).one()
        assert (run.trigger, run.principal, run.role, run.incident_id) == ("incident", "agent", "operator", critical)
        assert run.status == "completed"
    assert SYSTEM_CALLER.role == "operator"


# ---- the API


def test_agents_are_off_until_an_administrator_turns_them_on(settings, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    client = _client(settings, sessions)
    try:
        status = client.get("/api/v1/agents/status", headers=OPERATOR).json()
        assert (status["enabled"], status["can_run"]) == (False, False)
        assert [pack["name"] for pack in status["packs"]] == ["core", "performance", "configuration"]
        assert client.post(f"/api/v1/databases/{target_id}/snapshots", json={}, headers=OPERATOR).status_code == 409
        assert client.post("/api/v1/agents/investigations", json={"target_id": str(target_id)}, headers=OPERATOR).status_code == 409
        assert client.post("/api/v1/agents/snapshots/fleet", headers=OPERATOR).status_code == 409
        assert client.get(f"/api/v1/databases/{target_id}/snapshots", headers=OPERATOR).json()["enabled"] is False
    finally:
        client.__exit__(None, None, None)


def test_snapshot_routes_collect_compare_and_enforce_permissions(on, sessions, make_target, monkeypatch) -> None:
    target_id = make_target(alias="FINANCE")
    used = {"value": "71"}

    async def read(_settings, _alias, kinds):
        return {
            kind: ActivityReading(kind, TOOL_NAMES[kind], "ok", [
                {"source": "dba_tablespaces", "tablespace_name": "USERS", "used_pct": used["value"], "username": "APP"},
            ], "")
            for kind in kinds
        }

    monkeypatch.setattr(collector_module, "read_kinds", read)
    client = _client(on, sessions)
    try:
        path = f"/api/v1/databases/{target_id}/snapshots"
        assert client.post(path, json={}, headers=VIEWER).status_code == 403
        assert client.post(path, json={"packs": ["configuration"]}, headers=OPERATOR).status_code == 403
        assert client.post(path, json={"packs": ["everything"]}, headers=OPERATOR).status_code == 422
        first = client.post(path, json={}, headers=OPERATOR)
        assert first.status_code == 200
        assert first.json()["answered"] == len(kinds_for(("core", "performance"), True))
        used["value"] = "97"
        assert client.post(path, json={"packs": ["core"]}, headers=OPERATOR).status_code == 200
        seen = client.get(path, headers=OPERATOR).json()
        assert seen["snapshot_count"] == 2
        assert seen["can_collect"] is True
        tablespace = next(item for item in seen["readings"] if item["kind"] == "tablespace")
        assert tablespace["changes"] == ["USERS used_pct went from 71 to 97"]
        sessions_row = next(item for item in seen["readings"] if item["kind"] == "sessions")
        assert (sessions_row["restricted"], sessions_row["hidden"], sessions_row["changes"]) == (True, True, [])
        as_admin = client.get(path, headers=ADMIN).json()
        admin_sessions = next(item for item in as_admin["readings"] if item["kind"] == "sessions")
        assert (admin_sessions["restricted"], admin_sessions["hidden"]) == (True, False)
        assert admin_sessions["previous_at"] is not None
        assert client.get(path, headers=VIEWER).json()["can_collect"] is False
        runs = client.get("/api/v1/agents/runs", headers=VIEWER).json()["items"]
        assert [(run["agent"], run["status"]) for run in runs] == [("collector", "completed"), ("collector", "completed")]
        with sessions() as session:
            actions = [row.action for row in session.scalars(select(AuditEvent))]
        assert actions.count("snapshot.collect") == 2
    finally:
        client.__exit__(None, None, None)


def test_a_collection_inside_the_minimum_gap_returns_429(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    client = _client(on.model_copy(update={"agent_snapshot_min_gap_seconds": 600}), sessions)
    try:
        path = f"/api/v1/databases/{target_id}/snapshots"
        assert client.post(path, json={}, headers=OPERATOR).status_code == 200
        again = client.post(path, json={}, headers=OPERATOR)
        assert again.status_code == 429
        assert "Try again in" in again.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_investigation_routes_run_in_the_background_and_show_the_timeline_and_finding(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    incident_id = _incident(sessions, target_id)
    client = _client(on, sessions)
    try:
        assert client.post("/api/v1/agents/investigations", json={"incident_id": str(incident_id)}, headers=VIEWER).status_code == 403
        assert client.post("/api/v1/agents/investigations", json={}, headers=OPERATOR).status_code == 422
        started = client.post("/api/v1/agents/investigations", json={"incident_id": str(incident_id)}, headers=OPERATOR)
        assert started.status_code == 202
        run_id = started.json()["id"]
        run = client.get(f"/api/v1/agents/runs/{run_id}", headers=VIEWER).json()
        assert (run["status"], run["agent"], run["trigger"], run["target_name"]) == ("completed", "investigator", "operator", "FINANCE")
        assert [step["kind"] for step in run["steps"]] == ["plan", "collect", "compare", "lookup", "verify", "finding"]
        finding = run["finding"]
        assert (finding["decision"], finding["written_by"], finding["hidden"]) == ("open", "rules", False)
        listed = client.get(f"/api/v1/agents/findings?incident_id={incident_id}", headers=VIEWER).json()["items"]
        assert [item["id"] for item in listed] == [finding["id"]]
        assert client.get("/api/v1/agents/status", headers=VIEWER).json()["open_findings"] == 1
        decision = f"/api/v1/agents/findings/{finding['id']}/decision"
        assert client.post(decision, json={"decision": "accepted"}, headers=VIEWER).status_code == 403
        assert client.post(decision, json={"decision": "maybe"}, headers=OPERATOR).status_code == 422
        accepted = client.post(decision, json={"decision": "accepted", "note": "Matches what we saw."}, headers=OPERATOR).json()
        assert (accepted["decision"], accepted["decided_by"], accepted["decision_note"]) == ("accepted", "olga", "Matches what we saw.")
        assert client.get("/api/v1/agents/findings", headers=VIEWER).json()["items"] == []
        assert client.get("/api/v1/agents/runs?incident_id=" + str(incident_id), headers=VIEWER).json()["items"][0]["id"] == run_id
        with sessions() as session:
            actions = {row.action for row in session.scalars(select(AuditEvent))}
        assert {"agent.run", "agent.finding.accept"} <= actions
    finally:
        client.__exit__(None, None, None)


def test_a_restricted_finding_and_its_restricted_lines_are_hidden_from_other_roles(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    message = "ORA-00060: deadlock detected while waiting for resource"
    first = _investigate(sessions, on, _incident(sessions, target_id, message), caller=ROOT)
    with sessions.begin() as session:
        for row in session.scalars(select(EvidenceSnapshot)):
            row.taken_at = row.taken_at - timedelta(hours=2)
            if row.kind == "sessions":
                row.rows = []
    model = _Model({"cause": "A session is blocked on FINANCE.", "confidence": "low", "evidence": [2, 3, 4, 5, 6]})
    second = _investigate(sessions, on, _incident(sessions, target_id, message), caller=ROOT, gateway=_gateway(model))
    assert first != second
    client = _client(on, sessions)
    try:
        as_admin = client.get(f"/api/v1/agents/runs/{second}", headers=ADMIN).json()
        as_operator = client.get(f"/api/v1/agents/runs/{second}", headers=OPERATOR).json()
        admin_compare = next(step for step in as_admin["steps"] if step["kind"] == "compare")
        operator_compare = next(step for step in as_operator["steps"] if step["kind"] == "compare")
        assert any(line.startswith("Sessions:") for line in admin_compare["lines"])
        assert not any(line.startswith("Sessions:") for line in operator_compare["lines"])
        assert operator_compare["restricted_hidden"] is True
        assert as_admin["finding"]["hidden"] is False
        assert as_admin["finding"]["summary"] == "A session is blocked on FINANCE."
        hidden = as_operator["finding"]
        assert (hidden["hidden"], hidden["evidence"], hidden["next_steps"]) == (True, [], [])
        assert "only an administrator" in hidden["summary"]
        decision = f"/api/v1/agents/findings/{hidden['id']}/decision"
        assert client.post(decision, json={"decision": "dismissed"}, headers=OPERATOR).status_code == 403
        assert client.post(decision, json={"decision": "dismissed"}, headers=ADMIN).status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_database_agent_settings_are_read_by_all_and_changed_by_an_administrator(on, sessions, make_target) -> None:
    target_id = make_target(alias="FINANCE")
    client = _client(on, sessions)
    try:
        path = f"/api/v1/databases/{target_id}/agent-settings"
        seen = client.get(path, headers=OPERATOR).json()
        assert seen["editable"] is False
        assert seen["items"][0]["key"] == "agents_enabled"
        assert all(item["overridden"] is False for item in seen["items"])
        body = {"key": "agent_snapshot_retention_days", "value": 5}
        assert client.put(path, json=body, headers=OPERATOR).status_code == 403
        assert client.put(path, json={"key": "agent_snapshot_retention_days", "value": 90}, headers=ADMIN).status_code == 422
        assert client.put(path, json={"key": "agent_max_steps", "value": 4}, headers=ADMIN).status_code == 422
        changed = client.put(path, json=body, headers=ADMIN).json()
        row = next(item for item in changed["items"] if item["key"] == "agent_snapshot_retention_days")
        assert (row["overridden"], row["value"], row["system_value"]) == (True, 5, 14)
        assert client.get(f"/api/v1/databases/{target_id}/snapshots", headers=OPERATOR).json()["retention_days"] == 5
        client.put(path, json={"key": "agents_enabled", "value": False}, headers=ADMIN)
        assert client.post(f"/api/v1/databases/{target_id}/snapshots", json={}, headers=OPERATOR).status_code == 409
        cleared = client.put(path, json={"key": "agent_snapshot_retention_days", "value": None}, headers=ADMIN).json()
        row = next(item for item in cleared["items"] if item["key"] == "agent_snapshot_retention_days")
        assert (row["overridden"], row["value"]) == (False, 14)
        with sessions() as session:
            outcomes = [row.outcome for row in session.scalars(select(AuditEvent).where(AuditEvent.action == "agent.settings"))]
        assert outcomes == ["changed", "changed", "cleared"]
    finally:
        client.__exit__(None, None, None)


def test_the_fleet_snapshot_collects_every_database_that_allows_it(on, sessions, make_target) -> None:
    make_target(alias="FINANCE")
    opted_out = make_target(alias="WAREHOUSE")
    make_target(alias="RETIRED", enabled=False)
    with sessions.begin() as session:
        set_override(session, on, opted_out, "agents_enabled", False, "sentinel")
    client = _client(on, sessions)
    try:
        assert client.post("/api/v1/agents/snapshots/fleet", headers=VIEWER).status_code == 403
        assert client.post("/api/v1/agents/snapshots/fleet", headers=OPERATOR).status_code == 202
        run = client.get("/api/v1/agents/runs", headers=OPERATOR).json()["items"][0]
        assert (run["agent"], run["scope_type"], run["status"]) == ("collector", "fleet", "completed")
        detail = client.get(f"/api/v1/agents/runs/{run['id']}", headers=OPERATOR).json()
        lines = detail["steps"][0]["lines"]
        assert any(line.startswith("FINANCE:") and "readings stored" in line for line in lines)
        assert "WAREHOUSE: not collected. Agents are turned off for this database." in lines
        assert not any(line.startswith("RETIRED") for line in lines)
    finally:
        client.__exit__(None, None, None)


def test_agent_settings_are_in_the_admin_configuration(on, sessions) -> None:
    client = _client(on, sessions)
    try:
        items = client.get("/api/v1/admin/config", headers=ADMIN).json()["items"]
        agents = {item["key"]: item for item in items if item["section"] == "Agents"}
        assert {"agents_enabled", "agent_snapshot_retention_days", "agent_auto_investigate", "agent_collect_sensitive"} <= set(agents)
        assert all(item["editable"] for item in agents.values())
    finally:
        client.__exit__(None, None, None)

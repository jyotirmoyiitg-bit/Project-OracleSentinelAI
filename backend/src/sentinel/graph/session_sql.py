"""SessionDB, one session: the statement it runs and the plan Oracle cached for it.

Two fixed statements are read on one Oracle session. The first binds the SID and serial number the portal
listed; the second binds the SQL_ID and child number the first returned. No text from a person is ever bound,
and nothing is explained again, so no plan table is written. The statement and its plan name tables and hold
literal values, so they are shown to an administrator only.
"""

import re
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel

from sentinel.adapters.oracle.activity import (
    BOUND_TEXT_LIMIT,
    ActivityAccessError,
    ActivityUnavailable,
    bind_value_allowed,
    collect_bound_rows,
    oracle_code,
)
from sentinel.collectors.normalization import utcnow
from sentinel.config.settings import Settings

STATEMENT_NOTE = "The statement of a session and its plan are shown to an administrator."
_SQL_ID = re.compile(r"^[0-9a-z]{13}$")

# (statement rows, plan rows, note) for one SID and serial number.
StatementReader = Callable[[Settings, str, int, int], Awaitable[tuple[list[dict[str, str]], list[dict[str, str]], str]]]


class PlanStep(BaseModel):
    id: int
    parent_id: int | None
    depth: int
    operation: str
    options: str
    object_owner: str
    object_name: str
    optimizer: str
    cost: int | None
    cardinality: int | None
    bytes: int | None


class SessionStatement(BaseModel):
    sid: int
    serial: int
    found: bool
    status: str = ""
    kind: str = "none"  # current, previous, or none
    sql_id: str = ""
    child_number: int | None = None
    text: str = ""
    truncated: bool = False
    text_note: str | None = None
    plan_hash_value: str = ""
    optimizer_mode: str = ""
    executions: int | None = None
    elapsed_seconds: float | None = None
    buffer_gets: int | None = None
    disk_reads: int | None = None
    rows_processed: int | None = None
    plan: list[PlanStep] = []
    plan_note: str | None = None
    collected_at: datetime
    limitation: str | None = None


def _int(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def assemble_statement(
    sid: int, serial: int, rows: list[dict[str, str]], plan_rows: list[dict[str, str]], note: str | None,
    collected_at: datetime | None = None,
) -> SessionStatement:
    at = collected_at or utcnow()
    if note and not rows:
        return SessionStatement(sid=sid, serial=serial, found=False, collected_at=at, limitation=note)
    if not rows:
        return SessionStatement(
            sid=sid, serial=serial, found=False, collected_at=at,
            text_note="This session has ended, or its SID now belongs to another session. Refresh the list.",
        )
    row = rows[0]
    kind = (row.get("statement_kind") or "none").strip()
    text = (row.get("sql_fulltext") or "").strip()
    sql_id = (row.get("sql_id") or "").strip()
    text_note = None
    if kind == "none":
        text_note = "This session has not run a statement that is still known to Oracle."
    elif not text:
        text_note = "The statement is no longer in the shared pool, so its text cannot be shown."
    elif kind == "previous":
        text_note = "The session is idle. This is the last statement it ran."
    steps = [
        PlanStep(
            id=_int(step.get("id")) or 0,
            parent_id=_int(step.get("parent_id")),
            depth=_int(step.get("depth")) or 0,
            operation=(step.get("operation") or "").strip(),
            options=(step.get("options") or "").strip(),
            object_owner=(step.get("object_owner") or "").strip(),
            object_name=(step.get("object_name") or "").strip(),
            optimizer=(step.get("optimizer") or "").strip(),
            cost=_int(step.get("cost")),
            cardinality=_int(step.get("cardinality")),
            bytes=_int(step.get("bytes")),
        )
        for step in plan_rows
    ]
    plan_note = None
    if note:
        plan_note = note
    elif text and not steps:
        plan_note = "Oracle holds no cached plan for this statement."
    return SessionStatement(
        sid=sid, serial=serial, found=True,
        status=(row.get("status") or "").strip().upper(),
        kind=kind, sql_id=sql_id, child_number=_int(row.get("child_number")),
        text=text, truncated=len(text) >= BOUND_TEXT_LIMIT, text_note=text_note,
        plan_hash_value=(row.get("plan_hash_value") or "").strip(),
        optimizer_mode=(row.get("optimizer_mode") or "").strip(),
        executions=_int(row.get("executions")), elapsed_seconds=_float(row.get("elapsed_seconds")),
        buffer_gets=_int(row.get("buffer_gets")), disk_reads=_int(row.get("disk_reads")),
        rows_processed=_int(row.get("rows_processed")),
        plan=sorted(steps, key=lambda step: step.id), plan_note=plan_note,
        collected_at=at,
    )


async def read_statement(settings: Settings, alias: str, sid: int, serial: int) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    """Read the statement and then its cached plan on one Oracle session."""
    import oracledb

    from sentinel.adapters.oracle.connection import OracleConnectionManager

    manager = OracleConnectionManager(settings)
    try:
        async with manager.connection_context(alias) as connection:
            rows, note = collect_bound_rows(connection, "get_session_statement", {"sid": sid, "serial": serial})
            plan: list[dict[str, str]] = []
            plan_note = ""
            first = rows[0] if rows else {}
            sql_id = (first.get("sql_id") or "").strip()
            child = _int(first.get("child_number"))
            if _SQL_ID.fullmatch(sql_id) and child is not None and (first.get("sql_fulltext") or "").strip():
                try:
                    plan, plan_note = collect_bound_rows(connection, "get_statement_plan", {"sql_id": sql_id, "child": child})
                except ActivityAccessError as exc:
                    plan_note = f"Grant SELECT on {exc.object_name} to the monitoring account to see the plan."
            return rows, plan, " ".join(part for part in (note, plan_note) if part)
    except (ActivityAccessError, ActivityUnavailable):
        raise
    except oracledb.Error as exc:
        raise ActivityUnavailable(oracle_code(exc)) from None


_SYNTHETIC_TEXT = {
    "previous": "DELETE FROM LOANS.LOAN_FOLDER WHERE FOLDER_MODE = 'BATCHCTL' AND FOLDER_FORMAT = 'C0108' "
                "AND FOLDER_JOB = '003156' AND FOLDER_LEVEL = 'F'",
    "current": "SELECT a.account_id, a.balance, c.customer_name FROM loans.accounts a JOIN loans.customers c "
               "ON c.customer_id = a.customer_id WHERE a.branch_id = :b1 AND a.status = 'OPEN' ORDER BY a.balance DESC",
}
_SYNTHETIC_PLANS = {
    "previous": [
        {"id": "0", "parent_id": "", "depth": "0", "operation": "DELETE STATEMENT", "options": "", "object_owner": "",
         "object_name": "", "optimizer": "ALL_ROWS", "cost": "2", "cardinality": "", "bytes": ""},
        {"id": "1", "parent_id": "0", "depth": "1", "operation": "DELETE", "options": "", "object_owner": "LOANS",
         "object_name": "LOAN_FOLDER", "optimizer": "", "cost": "", "cardinality": "", "bytes": ""},
        {"id": "2", "parent_id": "1", "depth": "2", "operation": "TABLE ACCESS", "options": "FULL", "object_owner": "LOANS",
         "object_name": "LOAN_FOLDER", "optimizer": "", "cost": "2", "cardinality": "1", "bytes": "32"},
    ],
    "current": [
        {"id": "0", "parent_id": "", "depth": "0", "operation": "SELECT STATEMENT", "options": "", "object_owner": "",
         "object_name": "", "optimizer": "ALL_ROWS", "cost": "14", "cardinality": "", "bytes": ""},
        {"id": "1", "parent_id": "0", "depth": "1", "operation": "SORT", "options": "ORDER BY", "object_owner": "",
         "object_name": "", "optimizer": "", "cost": "14", "cardinality": "40", "bytes": "2400"},
        {"id": "2", "parent_id": "1", "depth": "2", "operation": "HASH JOIN", "options": "", "object_owner": "",
         "object_name": "", "optimizer": "", "cost": "13", "cardinality": "40", "bytes": "2400"},
        {"id": "3", "parent_id": "2", "depth": "3", "operation": "TABLE ACCESS", "options": "BY INDEX ROWID BATCHED",
         "object_owner": "LOANS", "object_name": "ACCOUNTS", "optimizer": "", "cost": "6", "cardinality": "40", "bytes": "960"},
        {"id": "4", "parent_id": "3", "depth": "4", "operation": "INDEX", "options": "RANGE SCAN", "object_owner": "LOANS",
         "object_name": "ACCOUNTS_BRANCH_IX", "optimizer": "", "cost": "2", "cardinality": "40", "bytes": ""},
        {"id": "5", "parent_id": "2", "depth": "3", "operation": "TABLE ACCESS", "options": "FULL", "object_owner": "LOANS",
         "object_name": "CUSTOMERS", "optimizer": "", "cost": "7", "cardinality": "5000", "bytes": "180000"},
    ],
}


def _synthetic(sid: int, serial: int) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    from sentinel.graph.sections import _SYNTHETIC_SESSIONS

    session = next((row for row in _SYNTHETIC_SESSIONS if _int(row.get("sid")) == sid
                    and _int(row.get("serial_no")) == serial), None)
    if session is None:
        return [], [], ""
    kind = "current" if session.get("status") == "ACTIVE" else "previous"
    if sid == 312:  # an idle SQL*Plus session whose last statement has aged out
        return [{"sid": str(sid), "serial_no": str(serial), "status": session["status"], "statement_kind": "previous",
                 "sql_id": "0s4ruucw2wvsw", "child_number": "0", "sql_fulltext": ""}], [], ""
    row = {
        "sid": str(sid), "serial_no": str(serial), "status": session["status"], "statement_kind": kind,
        "sql_id": session.get("sql_id") or "9babjv8yq8ru3", "child_number": "0", "sql_fulltext": _SYNTHETIC_TEXT[kind],
        "plan_hash_value": "1987654321" if kind == "current" else "3043255311", "optimizer_mode": "ALL_ROWS",
        "executions": "128" if kind == "current" else "1", "elapsed_seconds": "3.42" if kind == "current" else "0.01",
        "buffer_gets": "48211" if kind == "current" else "7", "disk_reads": "112" if kind == "current" else "0",
        "rows_processed": "5120" if kind == "current" else "0",
    }
    return [row], [dict(step) for step in _SYNTHETIC_PLANS[kind]], ""


async def load_statement(
    settings: Settings, alias: str, sid: int, serial: int, reader: StatementReader | None = None,
) -> SessionStatement:
    """The statement a session is running, or ran last, and its cached plan."""
    if not (bind_value_allowed(sid) and bind_value_allowed(serial)):
        raise ValueError("SID and serial number must be whole numbers")
    if reader is None and settings.synthetic_mode:
        rows, plan, note = _synthetic(sid, serial)
        return assemble_statement(sid, serial, rows, plan, note or None)
    try:
        rows, plan, note = await (reader or read_statement)(settings, alias, sid, serial)
    except ActivityAccessError as exc:
        return assemble_statement(sid, serial, [], [], f"Grant SELECT on {exc.object_name} to the monitoring account, then ask again.")
    except ActivityUnavailable as exc:
        text = "The database could not be read just now."
        return assemble_statement(sid, serial, [], [], f"{text} Oracle returned error {exc.code}." if exc.code is not None else text)
    return assemble_statement(sid, serial, rows, plan, note or None)

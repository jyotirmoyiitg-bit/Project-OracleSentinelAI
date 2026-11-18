"""Investigation playbooks: which readings and stored data fit which kind of incident.

A playbook is a fixed list. The model may choose a playbook by name; it cannot add a step.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Playbook:
    name: str
    title: str
    description: str
    pattern: str
    kinds: tuple[str, ...]
    metrics: tuple[str, ...]
    query: str
    next_steps: tuple[str, ...]


PLAYBOOKS: tuple[Playbook, ...] = (
    Playbook(
        "archive_destination", "Archive destination or recovery area full",
        "The archiver cannot write, or the recovery area is close to its limit.",
        r"ora-00257|ora-16038|ora-16014|ora-19809|ora-19815|archiv|recovery area|\bfra\b|flash recovery",
        ("recovery", "backups", "redo_logs", "ora_errors", "availability"),
        ("storage.fra.used_pct", "storage.fra.space_reclaimable", "storage.archive.dest_status", "storage.archive.error"),
        "archiver stuck recovery area full archived logs backup",
        (
            "Check when archived logs were last backed up and removed.",
            "Check the recovery area limit against the space used and the space that can be reclaimed.",
            "Check whether redo volume is higher than usual.",
        ),
    ),
    Playbook(
        "tablespace_full", "Tablespace full or unable to extend",
        "A segment cannot extend, or a tablespace is close to full.",
        r"ora-0165\d|ora-01688|ora-0169[12]|ora-30036|unable to extend|tablespace",
        ("tablespace", "datafile_sizes", "growth", "ora_errors"),
        ("storage.tablespace.used_pct", "storage.tablespace.autoextend", "storage.temp.used_pct"),
        "tablespace full unable to extend datafile autoextend",
        (
            "Check which tablespace is full and whether its datafiles can autoextend.",
            "Check the growth rate and the days left before it is full.",
            "Check free space on the storage that holds the datafiles.",
        ),
    ),
    Playbook(
        "blocking_sessions", "Blocking sessions or lock waits",
        "Sessions are waiting on a lock held by another session.",
        r"ora-00060|deadlock|blocking|blocked session|lock wait|\benq:",
        ("blocking", "sessions", "session_programs", "performance"),
        (),
        "blocking session lock wait deadlock",
        (
            "Identify the session at the head of the blocking chain and what it is running.",
            "Ask the owner of that session whether it can commit, roll back, or end.",
        ),
    ),
    Playbook(
        "session_limit", "Session or process limit reached",
        "New connections are refused because a session or process limit is reached.",
        r"ora-00018|ora-00020|ora-12516|ora-12520|maximum number of (sessions|processes)|processes limit|sessions limit|resources\.",
        ("session_programs", "sessions", "performance", "availability"),
        ("resources.used_pct", "resources.limit.current", "resources.limit.maximum"),
        "maximum number of processes exceeded sessions connections",
        (
            "Check which program holds the most sessions and whether they are active or idle.",
            "Check the process and session limits against current use.",
        ),
    ),
    Playbook(
        "memory_pressure", "Memory pressure",
        "The shared pool, SGA, or PGA cannot give memory to a request.",
        r"ora-04031|ora-04030|ora-00845|ora-04036|shared pool|\bsga\b|\bpga\b|out of process memory",
        ("memory", "performance", "top_sql", "ora_errors"),
        (),
        "ORA-04031 shared pool memory SGA PGA",
        (
            "Check SGA and PGA use against their targets.",
            "Check whether one statement or program is using most of the memory.",
        ),
    ),
    Playbook(
        "unavailable", "Database unavailable or restarted",
        "The instance cannot be reached, is not open, or was restarted.",
        (
            r"ora-01034|ora-01033|ora-01089|ora-01109|ora-12541|ora-12514|ora-12170|ora-03113|ora-03114|"
            r"unreachable|not available|connectivity|instance (is )?down|shutdown|startup"
        ),
        ("availability", "ora_errors", "background"),
        ("connectivity.available", "instance.status", "instance.database_status", "database.open_mode"),
        "instance down startup shutdown listener connection",
        (
            "Check the instance status and when it last started.",
            "Check the alert log lines around the time the database stopped answering.",
            "Check the listener on the database host.",
        ),
    ),
    Playbook(
        "backup_failure", "Backup failure",
        "A backup did not finish or has not run.",
        r"ora-19\d{3}|ora-27\d{3}|\brman\b|backup",
        ("backups", "recovery", "failed_jobs", "ora_errors"),
        ("storage.fra.used_pct",),
        "RMAN backup failed",
        (
            "Check the last successful backup and the error of the last failed one.",
            "Check free space in the backup destination.",
        ),
    ),
    Playbook(
        "job_failure", "Scheduler job failure",
        "A scheduler job failed.",
        r"ora-12012|scheduler job|job failed|dbms_scheduler|dbms_job",
        ("failed_jobs", "jobs", "ora_errors"),
        (),
        "scheduler job failed ORA-12012",
        (
            "Check which job failed, when, and the error it logged.",
            "Check whether the job has failed before.",
        ),
    ),
)
GENERAL = Playbook(
    "general", "General health check",
    "No specific playbook matched, so the main readings are collected.",
    r"$^",
    ("availability", "tablespace", "memory", "session_programs", "ora_errors", "performance"),
    ("connectivity.available", "storage.tablespace.used_pct", "storage.fra.used_pct", "resources.used_pct"),
    "",
    (
        "Review what changed since the previous snapshot.",
        "Check the alert log errors listed in the evidence.",
    ),
)
BY_NAME = {item.name: item for item in (*PLAYBOOKS, GENERAL)}
_ORA = re.compile(r"(?i)\bORA-\d{5}\b")


def match_playbook(text: str) -> Playbook | None:
    """The first playbook whose pattern is in the incident text or the operator's goal."""
    folded = text.lower()
    for item in PLAYBOOKS:
        if re.search(item.pattern, folded):
            return item
    return None


def ora_codes(text: str) -> list[str]:
    return list(dict.fromkeys(match.upper() for match in _ORA.findall(text)))

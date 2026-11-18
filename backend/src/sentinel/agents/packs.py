"""Snapshot packs: named groups of the existing fixed readings."""

from sentinel.ai.activity import ADMIN_ONLY_READINGS, TOOL_NAMES

PACKS: dict[str, tuple[str, ...]] = {
    "core": (
        "availability", "sessions", "session_programs", "blocking", "memory", "tablespace",
        "datafile_sizes", "redo_logs", "jobs", "backups", "recovery", "ora_errors",
    ),
    "performance": ("performance", "sql_workload", "top_sql"),
    "configuration": (
        "memory_config", "security", "database_users", "control_files", "datafiles", "segments", "dataguard",
    ),
}
PACK_LABELS = {
    "core": "Core",
    "performance": "Performance",
    "configuration": "Configuration and security",
}
# Readings that hold SQL text or session user names. Stored only when the database allows it.
SENSITIVE_KINDS = frozenset({"sessions", "blocking", "sql_workload", "top_sql", "security", "database_users"})
# Stored rows of these readings are readable by an admin operator only. This is a fixed rule, not a setting.
RESTRICTED_KINDS = SENSITIVE_KINDS | ADMIN_ONLY_READINGS | frozenset(PACKS["configuration"])
# Readings an investigation may also ask for. They are not part of a pack.
EXTRA_KINDS = ("failed_jobs", "background", "growth")
KIND_LABELS = {
    "availability": "Availability",
    "sessions": "Sessions",
    "session_programs": "Connected programs",
    "blocking": "Blocking sessions",
    "memory": "Memory",
    "tablespace": "Tablespaces",
    "datafile_sizes": "Datafile sizes",
    "redo_logs": "Redo logs",
    "jobs": "Running jobs",
    "failed_jobs": "Failed jobs",
    "backups": "Backups",
    "recovery": "Recovery",
    "ora_errors": "Alert log errors",
    "performance": "Performance",
    "sql_workload": "SQL workload",
    "top_sql": "Top SQL",
    "memory_config": "Memory configuration",
    "security": "Security",
    "database_users": "Database users",
    "control_files": "Control files",
    "datafiles": "Datafiles",
    "segments": "Largest segments",
    "dataguard": "Data Guard",
    "background": "Background processes",
    "growth": "Tablespace growth",
}
ALL_KINDS = tuple(dict.fromkeys([kind for kinds in PACKS.values() for kind in kinds] + list(EXTRA_KINDS)))
assert set(ALL_KINDS) <= set(TOOL_NAMES), "every snapshot reading must be an allowlisted fixed reading"


def is_restricted(kind: str) -> bool:
    return kind in RESTRICTED_KINDS


def pack_of(kind: str) -> str:
    for name, kinds in PACKS.items():
        if kind in kinds:
            return name
    return "investigation"


def label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind.replace("_", " ").capitalize())


def parse_packs(value: object) -> tuple[str, ...]:
    """Pack names from a comma-separated string or a list. Unknown names raise ValueError."""
    if isinstance(value, str):
        names = [item.strip().lower() for item in value.split(",") if item.strip()]
    else:
        items = value if isinstance(value, (list, tuple)) else []
        names = [str(item).strip().lower() for item in items if str(item).strip()]
    names = list(dict.fromkeys(names))
    if not names or set(names) - set(PACKS):
        raise ValueError("Packs must be chosen from core, performance, and configuration.")
    return tuple(names)


def kinds_for(packs: tuple[str, ...], collect_sensitive: bool) -> list[str]:
    """Readings of the packs, in pack order. Sensitive readings are left out when the database does not store them."""
    kinds: list[str] = []
    for name in packs:
        for kind in PACKS.get(name, ()):
            if kind in kinds or (kind in SENSITIVE_KINDS and not collect_sensitive):
                continue
            kinds.append(kind)
    return kinds

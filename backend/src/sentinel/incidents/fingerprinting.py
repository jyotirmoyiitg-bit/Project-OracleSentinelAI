"""Fingerprinting groups recurring rule firings into one deduplicated incident."""

import hashlib
import uuid


def compute_fingerprint(rule_id: str, target_id: uuid.UUID | str, scope_key: str) -> str:
    """Stable fingerprint for `rule_id + target_id + scope_key` (e.g. a tablespace name,
    an ORA- code, or empty string for target-level rules)."""
    raw = f"{rule_id}:{target_id}:{scope_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

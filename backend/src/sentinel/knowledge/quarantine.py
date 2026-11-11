"""Move unsafe files aside and append a reason to the quarantine log."""

import json
import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from sentinel.collectors.normalization import utcnow
from sentinel.knowledge.paths import display_relative
from sentinel.knowledge.types import QuarantineEvent

logger = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def scan_malware(scanner: str, path: Path) -> str | None:
    """Return 'malware' when a configured scanner reports a hit. A missing scanner does not block ingestion."""
    command = scanner.strip()
    if not command:
        return None
    executable = Path(command)
    if not executable.is_file():
        logger.info("Malware scanner is not available at %s", executable)
        return None
    try:
        completed = subprocess.run(
            [str(executable), str(path)],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Malware scanner could not read %s", path.name)
        return None
    if completed.returncode == 0:
        return None
    if completed.returncode == 1:
        return "malware"
    logger.warning("Malware scanner returned status %s for %s", completed.returncode, path.name)
    return None


def quarantine_file(root: Path, source: Path, relative: str, reason: str, detail: str) -> QuarantineEvent:
    destination_dir = root / "Quarantine"
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe = _SAFE_NAME.sub("_", Path(relative).name).strip("._")[:60] or "document"
    destination = destination_dir / f"{stamp}-{uuid.uuid4().hex[:8]}-{reason}-{safe}"
    shutil.move(str(source), str(destination))
    record = {
        "timestamp": utcnow().isoformat() + "Z",
        "source": relative,
        "destination": display_relative(root, destination),
        "reason": reason,
        "detail": detail[:500],
    }
    with (destination_dir / "quarantine.log").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    logger.info("Quarantined %s (%s)", relative, reason)
    return QuarantineEvent(relative, reason, detail[:500], record["destination"])

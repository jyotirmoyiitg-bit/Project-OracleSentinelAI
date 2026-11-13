"""Scan ora-19c-manual/ and Policy/ for added, changed, and unsafe files."""

import hashlib
import logging
import os
from pathlib import Path

from sentinel.config.settings import Settings
from sentinel.knowledge.constants import ALLOWED_EXTENSIONS, INTAKE_FOLDERS
from sentinel.knowledge.paths import display_relative, ensure_layout, intake_root, is_within
from sentinel.knowledge.quarantine import quarantine_file, scan_malware
from sentinel.knowledge.types import DiscoveredFile, Discovery

logger = logging.getLogger(__name__)
_MAX_RELATIVE_LENGTH = 700


def discover(settings: Settings) -> Discovery:
    root = intake_root(settings)
    ensure_layout(root)
    found = Discovery()
    for folder, category in sorted(INTAKE_FOLDERS.items()):
        _walk_category(root, root / folder, folder, category, settings, found)
    return found


def _walk_category(
    root: Path, base: Path, folder: str, category: str, settings: Settings, found: Discovery,
) -> None:
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink() or not is_within(base, child):
                _quarantine(root, child, "symlink" if child.is_symlink() else "path_escape",
                            "directory links are not followed", found)
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            _consider_file(root, base, folder, category, current / name, settings, found)


def _consider_file(
    root: Path, base: Path, folder: str, category: str, path: Path, settings: Settings, found: Discovery,
) -> None:
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        found.skipped += 1
        return
    relative = display_relative(root, path)
    if path.is_symlink() or not is_within(base, path) or not relative.startswith(f"{folder}/"):
        _quarantine(root, path, "symlink" if path.is_symlink() else "path_escape",
                    "file is linked or resolves outside its category folder", found)
        return
    if len(relative) > _MAX_RELATIVE_LENGTH:
        _quarantine(root, path, "path_escape", "path is too long", found)
        return
    try:
        size = path.stat().st_size
    except OSError as exc:
        _quarantine(root, path, "corrupt", exc.__class__.__name__, found)
        return
    if size > settings.knowledge_max_file_bytes:
        _quarantine(root, path, "too_large", f"{size} bytes exceeds the configured limit", found)
        return
    if scan_malware(settings.knowledge_malware_scanner, path):
        _quarantine(root, path, "malware", "scanner reported an unsafe file", found)
        return
    try:
        digest = _sha256(path)
    except OSError as exc:
        _quarantine(root, path, "corrupt", exc.__class__.__name__, found)
        return
    found.files.append(DiscoveredFile(path, relative, category, size, digest))


def _quarantine(root: Path, source: Path, reason: str, detail: str, found: Discovery) -> None:
    relative = display_relative(root, source)
    try:
        found.quarantined.append(quarantine_file(root, source, relative, reason, detail))
    except OSError as exc:
        logger.warning("Could not quarantine %s", relative)
        found.errors.append(f"{relative}: {exc.__class__.__name__}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

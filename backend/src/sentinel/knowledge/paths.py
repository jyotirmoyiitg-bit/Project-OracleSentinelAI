"""Resolve the intake folder and reject paths that leave it."""

from pathlib import Path

from sentinel.config.settings import Settings
from sentinel.knowledge.constants import CATEGORIES

_MAX_RELATIVE_LENGTH = 700


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def intake_root(settings: Settings) -> Path:
    directory = Path(settings.knowledge_intake_path)
    if not directory.is_absolute():
        directory = project_root() / directory
    return directory


def ensure_layout(root: Path) -> None:
    for name in (*sorted(CATEGORIES), "Quarantine"):
        (root / name).mkdir(parents=True, exist_ok=True)


def display_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def is_within(root: Path, path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def validate_requested_path(relative: str) -> str:
    """Accept only a Knowledge/ or Policy/ file path with no traversal."""
    if not relative or relative.startswith(("/", "\\")) or ":" in relative:
        raise ValueError("Knowledge paths must stay inside Knowledge/ or Policy/")
    parts = Path(relative).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Knowledge paths must stay inside Knowledge/ or Policy/")
    if parts[0] not in CATEGORIES:
        raise ValueError("Knowledge paths must stay inside Knowledge/ or Policy/")
    normalized = Path(*parts).as_posix()
    if len(normalized) > _MAX_RELATIVE_LENGTH:
        raise ValueError("Knowledge path is too long")
    return normalized

"""Slow down repeated failed sign-ins. Counters live in this process and hold no password."""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Entry:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class LoginThrottle:
    """Lock one username-and-address pair after too many failures inside the window.

    The pair, not the username alone, is locked, so one address cannot lock an operator out
    from every other address. A deployment with several API processes needs a shared store.
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 900, lock_seconds: int = 900) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lock_seconds = lock_seconds
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._guard = threading.Lock()

    @staticmethod
    def _key(username: str, address: str) -> tuple[str, str]:
        return username.strip().lower()[:80], address[:64]

    def retry_after(self, username: str, address: str, now: float | None = None) -> int:
        """Seconds until this pair may try again. Zero means it may try now."""
        moment = time.monotonic() if now is None else now
        with self._guard:
            entry = self._entries.get(self._key(username, address))
            if entry is None or entry.locked_until <= moment:
                return 0
            return max(1, int(entry.locked_until - moment))

    def failed(self, username: str, address: str, now: float | None = None) -> bool:
        """Record one failure. Returns True when this failure locked the pair."""
        moment = time.monotonic() if now is None else now
        with self._guard:
            self._prune(moment)
            entry = self._entries.setdefault(self._key(username, address), _Entry())
            entry.failures = [stamp for stamp in entry.failures if moment - stamp < self.window_seconds]
            entry.failures.append(moment)
            if len(entry.failures) >= self.max_failures:
                entry.locked_until = moment + self.lock_seconds
                entry.failures = []
                return True
            return False

    def succeeded(self, username: str, address: str) -> None:
        with self._guard:
            self._entries.pop(self._key(username, address), None)

    def _prune(self, moment: float) -> None:
        if len(self._entries) < 5000:
            return
        stale = [
            key for key, entry in self._entries.items()
            if entry.locked_until <= moment and all(moment - stamp >= self.window_seconds for stamp in entry.failures)
        ]
        for key in stale:
            self._entries.pop(key, None)

"""Writer protocol and shared write-report bookkeeping."""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from skrooge2firefly.model.mapper import Mapper


class WriteReport:
    """Accumulates created/skipped/failed counts and errors across a run."""

    def __init__(self) -> None:
        """Initialize empty counters."""
        self.counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        )
        self.errors: list[tuple[str, str]] = []

    def created(self, kind: str) -> None:
        """Record a created record of ``kind``."""
        self.counts[kind]["created"] += 1

    def updated(self, kind: str) -> None:
        """Record an updated (re-synced) record of ``kind``."""
        self.counts[kind]["updated"] += 1

    def skipped(self, kind: str) -> None:
        """Record a skipped (already-present) record of ``kind``."""
        self.counts[kind]["skipped"] += 1

    def failed(self, kind: str, message: str) -> None:
        """Record a failed record of ``kind`` with an error ``message``."""
        self.counts[kind]["failed"] += 1
        self.errors.append((kind, message))

    @property
    def has_failures(self) -> bool:
        """True if any record failed."""
        return any(c["failed"] for c in self.counts.values())

    def summary(self) -> str:
        """Return a human-readable multi-line summary."""
        lines = []
        for kind, c in sorted(self.counts.items()):
            lines.append(
                f"{kind:14s} created={c['created']:6d} updated={c['updated']:6d} "
                f"skipped={c['skipped']:6d} failed={c['failed']:6d}"
            )
        return "\n".join(lines)


class Writer(Protocol):
    """Emits a mapped IR to a target."""

    def write(self, mapper: Mapper, *, only: set[str] | None = None) -> WriteReport:
        """Write the IR; ``only`` optionally restricts to named sections."""
        ...

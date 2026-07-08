"""Read/write the orphan-decisions plan file (dry-run plans, real run applies)."""

from __future__ import annotations

import json
from pathlib import Path


def load(path: Path) -> dict[str, str]:
    """Return the ``{key: action}`` orphan map stored at ``path`` (``{}`` if absent)."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return dict(data.get("orphans", {}))


def save(path: Path, orphans: dict[str, str]) -> None:
    """Write ``orphans`` to ``path`` as ``{"orphans": orphans}``."""
    path.write_text(json.dumps({"orphans": orphans}, indent=2, sort_keys=True))

"""Deciders for resolving orphaned Firefly records during ``--update``.

An orphan is a record present in Firefly (tagged as ours) but absent from the
newer Skrooge file — for example a transaction the user deleted in Skrooge
since the last import. Only transactions and recurrences carry an ownership
marker (``external_id`` / the Skrooge notes marker), so only those two kinds
are ever candidates for deletion here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

_PROMPT = "[d]elete / [i]gnore / [D]elete all / [I]gnore all / [q]uit: "


class OrphanDecider(Protocol):
    """Decides what to do with a single orphaned record."""

    def decide(self, kind: str, key: str, label: str) -> str:
        """Return ``"delete"`` or ``"ignore"`` for the orphan identified by ``key``."""
        ...


class FixedDecider:
    """Always returns the same action, regardless of the orphan."""

    def __init__(self, action: str) -> None:
        """Store the action (``"delete"`` or ``"ignore"``) to always return."""
        self._action = action

    def decide(self, kind: str, key: str, label: str) -> str:
        """Return the fixed action."""
        return self._action


class PromptDecider:
    """Asks interactively, with sticky bulk actions (delete-all / ignore-all)."""

    def __init__(
        self,
        input_fn: Callable[[str], str],
        output_fn: Callable[[str], None],
    ) -> None:
        """Inject ``input_fn``/``output_fn`` so tests never touch real stdin/stdout."""
        self._input_fn = input_fn
        self._output_fn = output_fn
        self._bulk: str | None = None

    def decide(self, kind: str, key: str, label: str) -> str:
        """Prompt the user (unless a sticky bulk decision is already in effect)."""
        if self._bulk is not None:
            return self._bulk
        self._output_fn(f"Orphan {kind}: {label} ({key})")
        answer = self._input_fn(_PROMPT)
        if answer == "d":
            return "delete"
        if answer == "i":
            return "ignore"
        if answer == "D":
            self._bulk = "delete"
            return "delete"
        if answer == "I":
            self._bulk = "ignore"
            return "ignore"
        # "q" (or anything else): quit, treating all remaining orphans as ignored.
        self._bulk = "ignore"
        return "ignore"


class MappingDecider:
    """Looks up a decision by key in a mapping (e.g. loaded from a decisions file)."""

    def __init__(self, mapping: dict[str, str], fallback: OrphanDecider) -> None:
        """Store the ``key -> action`` mapping and the decider to fall back to."""
        self._mapping = mapping
        self._fallback = fallback

    def decide(self, kind: str, key: str, label: str) -> str:
        """Return the mapped action for ``key``, else defer to the fallback decider."""
        if key in self._mapping:
            return self._mapping[key]
        return self._fallback.decide(kind, key, label)

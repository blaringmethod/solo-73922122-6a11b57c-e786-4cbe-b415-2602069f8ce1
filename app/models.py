"""Domain objects shared by the importer, repository and reconciler."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Batch:
    code: str
    revision: int


@dataclass(frozen=True)
class Sheet:
    code: str
    total_pages: int
    # Sorted (batch_code, revision) pairs. A sheet with no entries is invalid
    # at import time and is ignored defensively by the reconciler.
    entries: tuple[tuple[str, int], ...] = field(default=())

    @property
    def batches(self) -> tuple[str, ...]:
        return tuple(code for code, _ in self.entries)


@dataclass(frozen=True)
class Submission:
    id: int
    created_at: str
    source: str
    note: str


@dataclass(frozen=True)
class Snapshot:
    submission: Submission
    batches: tuple[Batch, ...]
    sheets: tuple[Sheet, ...]

    @property
    def batch_by_code(self) -> dict[str, Batch]:
        return {b.code: b for b in self.batches}

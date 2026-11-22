"""Exact-cover reconciliation of page-count summary sheets.

Rules implemented here, in one place so the web layer cannot drift:

* Only sheets whose *every* recorded batch revision equals that batch's
  current revision in the same snapshot may be used.
* A combination of sheets is valid for the checked batches when every
  checked batch is counted exactly once and no unchecked batch appears.
* If every valid combination has the same page total, that total is reported.
* If valid combinations disagree, two witness combinations with different
  totals are returned.
* If there is no valid combination, no number can be produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Batch, Sheet, Snapshot


@dataclass(frozen=True)
class Witness:
    sheets: tuple[str, ...]
    total: int


@dataclass(frozen=True)
class StaleSheet:
    code: str
    details: tuple[str, ...]


@dataclass(frozen=True)
class CoverResult:
    status: str  # "ok" | "conflict" | "none"
    total: int | None
    witnesses: tuple[Witness, ...]
    combination_count: int
    usable_sheets: tuple[str, ...]
    stale_sheets: tuple[StaleSheet, ...]


def _stale_details(sheet: Sheet, current: dict[str, Batch]) -> tuple[str, ...]:
    details = []
    for code, revision in sheet.entries:
        batch = current.get(code)
        if batch is None:
            details.append(f"{code} 不在现行批次清单")
        elif batch.revision != revision:
            details.append(f"{code} 单上修订 r{revision} ≠ 现行 r{batch.revision}")
    return tuple(details)


def _exact_covers(
    selected: tuple[str, ...],
    sheets: Iterable[Sheet],
) -> tuple[int, list[Witness]]:
    """Enumerate exact covers restricted to the selected batch set.

    Bitmask DP over at most 16 selected batches (2^16 states). Each state
    keeps a minimum-total and a maximum-total witness. Summing a constant
    sheet total preserves ordering, so these bounds are exact for the full
    state, and ``min != max`` is exactly the existence of a disagreement.
    Cover counting is done separately and exactly.
    """
    index = {code: i for i, code in enumerate(selected)}
    selected_mask = (1 << len(selected)) - 1

    candidates: list[tuple[int, Sheet]] = []
    for sheet in sorted(sheets, key=lambda s: s.code):
        if not sheet.entries:
            continue
        mask = 0
        fits = True
        for code, _revision in sheet.entries:
            bit = index.get(code)
            if bit is None:  # extra batch outside the checked set
                fits = False
                break
            if mask & (1 << bit):  # duplicate entry, invalid sheet
                fits = False
                break
            mask |= 1 << bit
        if fits and mask:
            candidates.append((mask, sheet))

    if not candidates:
        return 0, []

    by_lowest: list[list[tuple[int, Sheet]]] = [[] for _ in range(len(selected))]
    for mask, sheet in candidates:
        lowest = (mask & -mask).bit_length() - 1
        by_lowest[lowest].append((mask, sheet))

    # lo/hi[mask] = (total, witness) with the smallest/largest achievable total.
    lo: list[tuple[int, tuple[str, ...]] | None] = [None] * (selected_mask + 1)
    hi: list[tuple[int, tuple[str, ...]] | None] = [None] * (selected_mask + 1)
    counts = [0] * (selected_mask + 1)
    counts[0] = 1

    for mask in range(1, selected_mask + 1):
        lowest = (mask & -mask).bit_length() - 1
        best_lo: tuple[int, tuple[str, ...]] | None = None
        best_hi: tuple[int, tuple[str, ...]] | None = None
        for sheet_mask, sheet in by_lowest[lowest]:
            if sheet_mask & ~mask:
                continue
            rest = mask ^ sheet_mask
            counts[mask] += counts[rest]
            if rest == 0:
                pairs = [(sheet.total_pages, (sheet.code,))]
            else:
                if lo[rest] is None or hi[rest] is None:
                    continue
                pairs = [
                    (lo[rest][0] + sheet.total_pages, lo[rest][1] + (sheet.code,)),
                    (hi[rest][0] + sheet.total_pages, hi[rest][1] + (sheet.code,)),
                ]
            for total, witness in pairs:
                if best_lo is None or total < best_lo[0]:
                    best_lo = (total, witness)
                if best_hi is None or total > best_hi[0]:
                    best_hi = (total, witness)
        lo[mask] = best_lo
        hi[mask] = best_hi

    count = counts[selected_mask]
    if count == 0 or lo[selected_mask] is None or hi[selected_mask] is None:
        return count, []

    low_total, low_witness = lo[selected_mask]
    high_total, high_witness = hi[selected_mask]
    if low_total == high_total:
        return count, [Witness(tuple(sorted(low_witness)), low_total)]
    return count, [
        Witness(tuple(sorted(low_witness)), low_total),
        Witness(tuple(sorted(high_witness)), high_total),
    ]


def stale_sheets_for(snapshot: Snapshot) -> tuple[StaleSheet, ...]:
    """Sheets whose recorded revisions do not all match the snapshot."""
    current = snapshot.batch_by_code
    stale = []
    for sheet in snapshot.sheets:
        details = _stale_details(sheet, current)
        if details:
            stale.append(StaleSheet(sheet.code, details))
    return tuple(stale)


def reconcile(snapshot: Snapshot, selected_codes: Iterable[str]) -> CoverResult:
    current = snapshot.batch_by_code
    selected_seen: list[str] = []
    for code in selected_codes:
        if code in current and code not in selected_seen:
            selected_seen.append(code)
    selected = tuple(sorted(selected_seen, key=lambda c: (c.casefold(), c)))

    usable: list[Sheet] = []
    stale = stale_sheets_for(snapshot)
    stale_codes = {s.code for s in stale}
    for sheet in snapshot.sheets:
        if sheet.code not in stale_codes:
            usable.append(sheet)

    requested = tuple(sorted(set(selected_codes)))
    ignored = tuple(sorted(code for code in requested if code not in current))

    usable_codes = tuple(s.code for s in usable)
    if not selected or ignored:
        return CoverResult("none", None, (), 0, usable_codes, tuple(stale))

    count, witnesses = _exact_covers(selected, usable)
    if count == 0 or not witnesses:
        return CoverResult("none", None, (), count, usable_codes, tuple(stale))
    if len(witnesses) == 1:
        return CoverResult(
            "ok", witnesses[0].total, tuple(witnesses), count,
            usable_codes, tuple(stale),
        )
    return CoverResult(
        "conflict", None, tuple(witnesses), count, usable_codes, tuple(stale)
    )

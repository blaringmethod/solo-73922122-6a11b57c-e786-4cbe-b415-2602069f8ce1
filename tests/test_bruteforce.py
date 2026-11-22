"""Randomised differential test: the DP must agree with brute-force covers."""

from __future__ import annotations

import random

from app.models import Batch, Sheet, Snapshot, Submission
from app.reconcile import reconcile


def brute_force(snapshot, selected):
    current = {b.code: b.revision for b in snapshot.batches}
    usable = [
        s
        for s in snapshot.sheets
        if all(current.get(c) == r for c, r in s.entries)
    ]
    wanted = set(selected)
    if not wanted or any(code not in current for code in selected):
        return "none", None, 0, set()

    totals = []
    for mask in range(1 << len(usable)):
        chosen = [usable[i] for i in range(len(usable)) if mask >> i & 1]
        codes = [code for sheet in chosen for code, _ in sheet.entries]
        if len(codes) != len(set(codes)) or set(codes) != wanted:
            continue
        totals.append(sum(sheet.total_pages for sheet in chosen))
    if not totals:
        return "none", None, 0, set()
    distinct = set(totals)
    if len(distinct) == 1:
        return "ok", totals[0], len(totals), distinct
    return "conflict", None, len(totals), distinct


def valid_witness(snapshot, selected, sheet_codes, total):
    by_code = {s.code: s for s in snapshot.sheets}
    current = {b.code: b.revision for b in snapshot.batches}
    chosen = [by_code[code] for code in sheet_codes]
    codes = [code for sheet in chosen for code, _ in sheet.entries]
    return (
        len(codes) == len(set(codes))
        and set(codes) == set(selected)
        and sum(sheet.total_pages for sheet in chosen) == total
        and all(current.get(code) == revision for sheet in chosen
                for code, revision in sheet.entries)
    )


def random_case(rng):
    size = rng.randint(1, 5)
    batches = [(f"B{i}", rng.choice([1, 2])) for i in range(size)]
    sheets = []
    for index in range(rng.randint(0, 8)):
        count = rng.randint(1, min(3, size))
        picked = rng.sample(batches, count)
        entries = [
            (code, revision if rng.random() < 0.7 else revision + 1)
            for code, revision in picked
        ]
        sheets.append((f"S{index}", rng.randint(1, 100), entries))
    snapshot = Snapshot(
        Submission(1, "x", "import", ""),
        tuple(Batch(code, rev) for code, rev in batches),
        tuple(Sheet(code, pages, tuple(sorted(e))) for code, pages, e in sheets),
    )
    selected = rng.sample([code for code, _ in batches], rng.randint(1, size))
    return snapshot, selected


def test_dp_matches_brute_force_on_random_cases():
    rng = random.Random(20261122)
    for trial in range(600):
        snapshot, selected = random_case(rng)
        expected = brute_force(snapshot, selected)
        result = reconcile(snapshot, selected)

        assert result.status == expected[0], f"trial {trial}"
        assert result.combination_count == expected[2], f"trial {trial}"
        if expected[0] == "ok":
            assert result.total == expected[1], f"trial {trial}"
        if expected[0] == "conflict":
            assert {w.total for w in result.witnesses} == {
                min(expected[3]),
                max(expected[3]),
            }, f"trial {trial}"
        for witness in result.witnesses:
            assert valid_witness(
                snapshot, selected, witness.sheets, witness.total
            ), f"trial {trial}"

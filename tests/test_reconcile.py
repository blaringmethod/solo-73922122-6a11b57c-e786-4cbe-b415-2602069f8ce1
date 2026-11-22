from app.reconcile import reconcile

from conftest import make_snapshot


def codes(result):
    return [witness.sheets for witness in result.witnesses]


def test_single_cover_reports_total(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 2), ("B02", 2), ("B03", 1)],
        [
            ("S1", 150, [("B01", 2), ("B02", 2)]),
            ("S2", 90, [("B03", 1)]),
        ],
    )
    result = reconcile(snap, ["B01", "B02", "B03"])
    assert result.status == "ok"
    assert result.total == 240
    assert result.combination_count == 1


def test_multiple_covers_with_same_total_are_ok(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1), ("B03", 1)],
        [
            ("S1", 100, [("B01", 1), ("B02", 1)]),
            ("S2", 100, [("B01", 1), ("B03", 1)]),
            ("S3", 100, [("B02", 1), ("B03", 1)]),
        ],
    )
    # B01+B02 can be covered by S1 only; B01+B03 only by S2; so a full
    # cover of all three needs 2 sheets: S1+S2? no. Use differing check.
    result = reconcile(snap, ["B01", "B02"])
    assert result.status == "ok"
    assert result.total == 100
    assert result.combination_count == 1


def test_overlapping_sheets_same_total_is_ok(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1)],
        [
            ("S1", 60, [("B01", 1), ("B02", 1)]),
            ("S2", 60, [("B01", 1), ("B02", 1)]),
        ],
    )
    result = reconcile(snap, ["B01", "B02"])
    assert result.status == "ok"
    assert result.total == 60
    assert result.combination_count == 2


def test_conflicting_totals_reported_as_two_witnesses(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1)],
        [
            ("S1", 60, [("B01", 1), ("B02", 1)]),
            ("S2", 75, [("B01", 1), ("B02", 1)]),
        ],
    )
    result = reconcile(snap, ["B01", "B02"])
    assert result.status == "conflict"
    assert result.total is None
    pairs = [(w.sheets, w.total) for w in result.witnesses]
    assert pairs == [(("S1",), 60), (("S2",), 75)]


def test_conflict_detected_across_different_partitions(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1), ("B03", 1)],
        [
            ("S1", 100, [("B01", 1), ("B02", 1)]),
            ("S2", 40, [("B03", 1)]),
            ("S3", 200, [("B01", 1), ("B02", 1)]),
        ],
    )
    result = reconcile(snap, ["B01", "B02", "B03"])
    assert result.status == "conflict"
    pairs = sorted((w.sheets, w.total) for w in result.witnesses)
    assert pairs == [(("S1", "S2"), 140), (("S2", "S3"), 240)]
    assert {w.total for w in result.witnesses} == {140, 240}


def test_equal_totals_across_different_partitions_is_ok(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1), ("B03", 1)],
        [
            ("S1", 100, [("B01", 1), ("B02", 1)]),
            ("S2", 40, [("B03", 1)]),
            ("S3", 140, [("B01", 1), ("B02", 1), ("B03", 1)]),
        ],
    )
    result = reconcile(snap, ["B01", "B02", "B03"])
    assert result.status == "ok"
    assert result.total == 140
    assert result.combination_count == 2


def test_extra_batches_exclude_sheet(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1), ("B03", 1)],
        [
            ("S1", 50, [("B01", 1)]),
            ("S2", 80, [("B02", 1), ("B03", 1)]),
        ],
    )
    # Asking only for B01 must not accept a sheet also containing B02.
    result = reconcile(snap, ["B01"])
    assert result.status == "ok"
    assert result.total == 50

    snap2 = snapshot_factory(
        [("B01", 1), ("B02", 1)],
        [("S1", 80, [("B01", 1), ("B02", 1)])],
    )
    result = reconcile(snap2, ["B01"])
    assert result.status == "none"


def test_missing_coverage_is_none(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1)],
        [("S1", 50, [("B01", 1)])],
    )
    result = reconcile(snap, ["B01", "B02"])
    assert result.status == "none"
    assert result.total is None
    assert result.combination_count == 0


def test_stale_sheet_ignored_and_reported(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 2), ("B02", 1)],
        [
            ("S1", 50, [("B01", 1)]),  # stale revision
            ("S2", 70, [("B01", 2), ("B02", 1)]),
        ],
    )
    result = reconcile(snap, ["B01"])
    assert result.status == "none"  # S2 has extra batch, S1 stale
    assert result.usable_sheets == ("S2",)
    assert result.stale_sheets[0].code == "S1"
    assert "r1" in result.stale_sheets[0].details[0]

    result = reconcile(snap, ["B01", "B02"])
    assert result.status == "ok"
    assert result.total == 70


def test_unknown_selected_batch_is_none(snapshot_factory):
    snap = snapshot_factory([("B01", 1)], [("S1", 10, [("B01", 1)])])
    result = reconcile(snap, ["B01", "B99"])
    assert result.status == "none"


def test_empty_selection_is_none(snapshot_factory):
    snap = snapshot_factory([("B01", 1)], [("S1", 10, [("B01", 1)])])
    assert reconcile(snap, []).status == "none"


def test_single_batch_counted_once_in_valid_cover(snapshot_factory):
    snap = snapshot_factory(
        [("B01", 1), ("B02", 1)],
        [
            ("S1", 40, [("B01", 1)]),
            ("S2", 40, [("B01", 1)]),
        ],
    )
    result = reconcile(snap, ["B01"])
    assert result.status == "ok"
    assert result.total == 40
    assert result.combination_count == 2


def test_pair_sheets_cannot_cover_five_batches(snapshot_factory):
    # 5 batches need an odd number of pair-sheets to partition them, which is
    # impossible, so there is no exact cover at all.
    batches = [(f"B{i:02d}", 1) for i in range(5)]
    sheets = []
    n = 0
    for i in range(5):
        for j in range(i + 1, 5):
            n += 1
            pages = 10 * (i + 1) + 10 * (j + 1)
            sheets.append((f"S{n:02d}", pages, [batches[i], batches[j]]))
    snap = snapshot_factory(batches, sheets)
    result = reconcile(snap, [code for code, _ in batches])
    assert result.status == "none"
    assert result.combination_count == 0


def test_many_disjoint_overlaps_keep_total_consistent(snapshot_factory):
    # Four batches, pair sheets plus two whole-set sheets; all totals agree.
    batches = [("B01", 1), ("B02", 1), ("B03", 1), ("B04", 1)]
    sheets = [
        ("S1", 30, [("B01", 1), ("B02", 1)]),
        ("S2", 70, [("B03", 1), ("B04", 1)]),
        ("S3", 100, [("B01", 1), ("B02", 1), ("B03", 1), ("B04", 1)]),
        ("S4", 40, [("B01", 1), ("B03", 1)]),
        ("S5", 60, [("B02", 1), ("B04", 1)]),
    ]
    snap = snapshot_factory(batches, sheets)
    result = reconcile(snap, [code for code, _ in batches])
    assert result.status == "ok"
    assert result.total == 100
    assert result.combination_count == 3
    assert all(witness.total == 100 for witness in result.witnesses)

import sqlite3

import pytest

from app.db import connect, init_db
from app.models import Batch, Sheet
from app.repository import (
    RevisionError,
    import_documents,
    latest_submission,
    list_submissions,
    load_current,
    load_snapshot,
    update_revision,
)


@pytest.fixture()
def conn(tmp_path):
    connection = connect(str(tmp_path / "repo.sqlite3"))
    init_db(connection)
    yield connection
    connection.close()


def seed(conn):
    return import_documents(
        conn,
        (Batch("B01", 2), Batch("B02", 2)),
        (Sheet("S1", 100, (("B01", 2), ("B02", 2))),),
        note="seed",
    )


def test_import_creates_one_snapshot(conn):
    sub_id = seed(conn)
    snapshot = load_snapshot(conn, sub_id)
    assert [b.code for b in snapshot.batches] == ["B01", "B02"]
    assert snapshot.sheets[0].total_pages == 100
    assert snapshot.sheets[0].entries == (("B01", 2), ("B02", 2))


def test_revision_update_clones_and_keeps_sheet_revisions(conn):
    base = seed(conn)
    new_id = update_revision(conn, base, "B01", 3)
    assert new_id != base
    old = load_snapshot(conn, base)
    new = load_snapshot(conn, new_id)
    assert {b.code: b.revision for b in old.batches}["B01"] == 2
    assert {b.code: b.revision for b in new.batches}["B01"] == 3
    # Sheets are copied verbatim, still recording the old revision.
    assert new.sheets == old.sheets
    assert latest_submission(conn).id == new_id


def test_revision_update_rejects_non_latest_base(conn):
    base = seed(conn)
    newer = update_revision(conn, base, "B01", 3)
    with pytest.raises(RevisionError):
        update_revision(conn, base, "B02", 5)
    # Nothing was written by the rejected attempt.
    assert latest_submission(conn).id == newer
    assert len(list_submissions(conn)) == 2


def test_revision_update_rejects_unknown_batch_and_bad_revision(conn):
    base = seed(conn)
    with pytest.raises(RevisionError):
        update_revision(conn, base, "B99", 1)
    with pytest.raises(RevisionError):
        update_revision(conn, base, "B01", 0)
    with pytest.raises(RevisionError):
        update_revision(conn, base, "B01", 2)  # already r2, no-op
    assert latest_submission(conn).id == base


def test_load_current_refuses_stale_expected_id(conn):
    base = seed(conn)
    update_revision(conn, base, "B01", 3)
    with pytest.raises(RevisionError):
        load_current(conn, base)
    latest, snapshot = load_current(conn, latest_submission(conn).id)
    assert snapshot.batches[0].revision == 3


def test_snapshot_is_immutable_after_later_import(conn):
    base = seed(conn)
    import_documents(conn, (Batch("C01", 1),), (Sheet("T1", 5, (("C01", 1),)),))
    again = load_snapshot(conn, base)
    assert [b.code for b in again.batches] == ["B01", "B02"]
    assert again.sheets[0].code == "S1"


def test_schema_triggers_enforce_limits(conn):
    from app.parsing import ImportError_

    with pytest.raises(ImportError_):
        import_documents(
            conn,
            tuple(Batch(f"B{i:02d}", 1) for i in range(17)),
            (),
        )
    assert latest_submission(conn) is None  # nothing written

    # The database itself also refuses over-limit rows, not just the app.
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO submissions (created_at, source) VALUES ('2026-01-01', 'import')"
    )
    sub_id = conn.execute("SELECT max(id) FROM submissions").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        for i in range(17):
            conn.execute(
                "INSERT INTO batches (submission_id, code, revision) VALUES (?, ?, 1)",
                (sub_id, f"B{i:02d}"),
            )
    conn.execute("ROLLBACK")
    assert latest_submission(conn) is None


def test_import_is_atomic_on_unknown_sheet_batch(conn):
    with pytest.raises(Exception):
        import_documents(
            conn,
            (Batch("B01", 1),),
            (Sheet("S1", 10, (("B99", 1),)),),
        )
    assert latest_submission(conn) is None

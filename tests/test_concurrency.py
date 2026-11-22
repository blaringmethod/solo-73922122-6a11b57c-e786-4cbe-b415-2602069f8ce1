"""Concurrent write behaviour: only one commit may extend a given base."""

from __future__ import annotations

import threading

import pytest

from app.db import connect, init_db
from app.models import Batch, Sheet
from app.repository import (
    RevisionError,
    import_documents,
    latest_submission,
    list_submissions,
    update_revision,
)


def test_concurrent_revision_updates_only_one_wins(tmp_path):
    path = str(tmp_path / "race.sqlite3")
    setup = connect(path)
    init_db(setup)
    base = import_documents(
        setup,
        (Batch("B01", 1), Batch("B02", 1)),
        (Sheet("S1", 10, (("B01", 1), ("B02", 1))),),
    )
    setup.close()

    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker(code: str) -> None:
        conn = connect(path)
        try:
            barrier.wait(timeout=5)
            update_revision(conn, base, code, 2)
            results.append("ok")
        except RevisionError:
            results.append("rejected")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(code,)) for code in ("B01", "B02")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == ["ok", "rejected"]

    conn = connect(path)
    try:
        # base + exactly one successful clone; no interleaved state.
        assert len(list_submissions(conn)) == 2
        assert latest_submission(conn).id == base + 1
        snapshot_ok = True
    finally:
        conn.close()
    assert snapshot_ok


def test_revision_rejects_base_that_is_not_latest(tmp_path):
    conn = connect(str(tmp_path / "seq.sqlite3"))
    init_db(conn)
    base = import_documents(
        conn,
        (Batch("B01", 1),),
        (Sheet("S1", 5, (("B01", 1),)),),
    )
    new = update_revision(conn, base, "B01", 2)
    with pytest.raises(RevisionError):
        update_revision(conn, base, "B01", 3)
    assert latest_submission(conn).id == new
    conn.close()

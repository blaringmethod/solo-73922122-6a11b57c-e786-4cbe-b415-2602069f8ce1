"""Persistence for submissions and their immutable snapshots."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import Batch, Sheet, Snapshot, Submission
from .parsing import MAX_BATCHES, MAX_SHEETS, ImportError_
from .db import read_tx, write_tx


class RevisionError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    source: str,
    note: str,
    batches: tuple[Batch, ...],
    sheets: tuple[Sheet, ...],
) -> int:
    if len(batches) > MAX_BATCHES:
        raise ImportError_([f"批次数 {len(batches)} 超过上限 {MAX_BATCHES}"])
    if len(sheets) > MAX_SHEETS:
        raise ImportError_([f"汇总单张数 {len(sheets)} 超过上限 {MAX_SHEETS}"])
    known = {batch.code for batch in batches}
    for sheet in sheets:
        unknown = [code for code in sheet.batches if code not in known]
        if unknown:
            raise ImportError_(
                [f"汇总单“{sheet.code}”含未知批次：{'、'.join(unknown)}"]
            )

    cur = conn.execute(
        "INSERT INTO submissions (created_at, source, note) VALUES (?, ?, ?)",
        (_now(), source, note),
    )
    submission_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT INTO batches (submission_id, code, revision) VALUES (?, ?, ?)",
        [(submission_id, b.code, b.revision) for b in batches],
    )
    conn.executemany(
        "INSERT INTO sheets (submission_id, code, total_pages) VALUES (?, ?, ?)",
        [(submission_id, s.code, s.total_pages) for s in sheets],
    )
    conn.executemany(
        """INSERT INTO sheet_entries
               (submission_id, sheet_code, batch_code, revision)
           VALUES (?, ?, ?, ?)""",
        [
            (submission_id, sheet.code, code, revision)
            for sheet in sheets
            for code, revision in sheet.entries
        ],
    )
    return submission_id


def latest_submission(conn: sqlite3.Connection) -> Submission | None:
    row = conn.execute(
        "SELECT id, created_at, source, note FROM submissions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return Submission(**dict(row)) if row is not None else None


def load_snapshot(conn: sqlite3.Connection, submission_id: int) -> Snapshot:
    """Load one snapshot inside a single read transaction."""
    with read_tx(conn):
        return _load_snapshot_in_tx(conn, submission_id)


def _load_snapshot_in_tx(
    conn: sqlite3.Connection, submission_id: int
) -> Snapshot:
    sub_row = conn.execute(
        "SELECT id, created_at, source, note FROM submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    if sub_row is None:
        raise LookupError(f"提交记录 {submission_id} 不存在")
    batch_rows = conn.execute(
        "SELECT code, revision FROM batches WHERE submission_id = ? ORDER BY code",
        (submission_id,),
    ).fetchall()
    sheet_rows = conn.execute(
        "SELECT code, total_pages FROM sheets WHERE submission_id = ? ORDER BY code",
        (submission_id,),
    ).fetchall()
    entry_rows = conn.execute(
        """SELECT sheet_code, batch_code, revision
             FROM sheet_entries
            WHERE submission_id = ?
            ORDER BY sheet_code, batch_code""",
        (submission_id,),
    ).fetchall()

    entries: dict[str, list[tuple[str, int]]] = {}
    for row in entry_rows:
        entries.setdefault(row["sheet_code"], []).append(
            (row["batch_code"], row["revision"])
        )
    sheets = tuple(
        Sheet(row["code"], row["total_pages"], tuple(entries.get(row["code"], ())))
        for row in sheet_rows
    )
    return Snapshot(
        submission=Submission(**dict(sub_row)),
        batches=tuple(Batch(row["code"], row["revision"]) for row in batch_rows),
        sheets=sheets,
    )


def latest_state(conn: sqlite3.Connection) -> tuple[Submission, Snapshot] | None:
    """Latest submission and its snapshot read from one consistent snapshot."""
    with read_tx(conn):
        latest = latest_submission(conn)
        if latest is None:
            return None
        return latest, _load_snapshot_in_tx(conn, latest.id)


def load_current(
    conn: sqlite3.Connection, expected_id: int
) -> tuple[Submission, Snapshot]:
    """Load the snapshot in one read transaction, refusing stale ids.

    Refusing an id that is no longer the newest commit is what keeps the web
    page from reconciling a batch list against sheets from a different
    committed state.
    """
    with read_tx(conn):
        latest = latest_submission(conn)
        if latest is None:
            raise RevisionError("尚未导入任何批次修订清单")
        if latest.id != expected_id:
            raise RevisionError(
                "页面状态已过期：已有新的提交，请刷新后重试"
            )
        return latest, _load_snapshot_in_tx(conn, latest.id)


def import_documents(
    conn: sqlite3.Connection, batches: tuple[Batch, ...], sheets: tuple[Sheet, ...],
    note: str = "",
) -> int:
    with write_tx(conn):
        return _insert_snapshot(
            conn, source="import", note=note, batches=batches, sheets=sheets
        )


def update_revision(
    conn: sqlite3.Connection, base_id: int, code: str, revision: int
) -> int:
    """Clone the latest snapshot with one batch's revision changed.

    Stored sheet revisions are left untouched: sheets keep the revision they
    were issued against, so the reconciliation can tell which ones went stale.
    """
    if revision <= 0:
        raise RevisionError("修订号必须为正整数")

    with write_tx(conn):
        latest = latest_submission(conn)
        if latest is None or latest.id != base_id:
            raise RevisionError(
                "页面上的清单已不是最新提交，请刷新后基于最新状态重试"
            )
        prev = _load_snapshot_in_tx(conn, base_id)
        if code not in prev.batch_by_code:
            raise RevisionError(f"批次“{code}”不在现行清单中")
        if prev.batch_by_code[code].revision == revision:
            raise RevisionError(f"批次“{code}”的现行修订号已是 r{revision}，无需更版")

        batches = tuple(
            Batch(b.code, revision if b.code == code else b.revision)
            for b in prev.batches
        )
        return _insert_snapshot(
            conn,
            source="revision",
            note=f"{code} 修订号改为 r{revision}",
            batches=batches,
            sheets=prev.sheets,
        )


def list_submissions(conn: sqlite3.Connection, limit: int = 20) -> list[Submission]:
    rows = conn.execute(
        "SELECT id, created_at, source, note FROM submissions ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Submission(**dict(row)) for row in rows]

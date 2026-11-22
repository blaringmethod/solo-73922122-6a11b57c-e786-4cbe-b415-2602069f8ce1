from __future__ import annotations

import pytest

from app.models import Batch, Sheet, Snapshot, Submission


def make_snapshot(batches, sheets, submission_id=1):
    return Snapshot(
        submission=Submission(submission_id, "2026-01-01T00:00:00+00:00", "import", ""),
        batches=tuple(Batch(code, rev) for code, rev in batches),
        sheets=tuple(
            Sheet(code, pages, tuple(sorted(entries))) for code, pages, entries in sheets
        ),
    )


@pytest.fixture()
def app(tmp_path):
    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.sqlite3"),
            "SECRET_KEY": "test",
        }
    )
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def snapshot_factory():
    return make_snapshot

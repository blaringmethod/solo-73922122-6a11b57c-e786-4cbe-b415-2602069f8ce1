"""HTTP views for import, revision update, and reconciliation."""

from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from . import repository
from .db import get_db
from .parsing import MAX_BATCHES, MAX_SHEETS, ImportError_, parse_and_check
from .reconcile import reconcile, stale_sheets_for

bp = Blueprint("main", __name__)

SAMPLE_BATCHES = """# 批次 修订号
B01 r2
B02 r2
B03 r1
B04 r1
"""

SAMPLE_SHEETS = """# 单号 总页数，随后是所含批次及各自修订号
S1 150
B01 r2
B02 r2

S2 90
B03 r1
B04 r1
"""


def _current_state(conn):
    state = repository.latest_state(conn)
    if state is None:
        return None, None
    return state


@bp.get("/healthz")
def healthz():
    conn = get_db()
    conn.execute("SELECT 1").fetchone()
    return {"status": "ok"}


@bp.get("/")
def index():
    conn = get_db()
    latest, snapshot = _current_state(conn)
    submissions = repository.list_submissions(conn, limit=8)
    selected = request.args.getlist("batch")
    result = None
    error = None
    if request.args.get("check") == "1":
        if latest is None:
            error = "尚未导入批次修订清单与汇总单，无法核对。"
        elif not selected:
            error = "请至少勾选一个待验批次。"
        else:
            try:
                # One read transaction: list and sheets share a snapshot id.
                latest, snapshot = repository.load_current(conn, latest.id)
                result = reconcile(snapshot, selected)
            except repository.RevisionError as exc:
                error = exc.message
    return render_template(
        "index.html",
        latest=latest,
        snapshot=snapshot,
        submissions=submissions,
        selected=selected,
        result=result,
        error=error,
        stale=stale_sheets_for(snapshot) if snapshot else (),
    )


def _render_import(batches_text: str, sheets_text: str, status: int = 200):
    return (
        render_template(
            "import.html",
            batches_text=batches_text,
            sheets_text=sheets_text,
            sample_batches=SAMPLE_BATCHES,
            sample_sheets=SAMPLE_SHEETS,
            max_batches=MAX_BATCHES,
            max_sheets=MAX_SHEETS,
        ),
        status,
    )


@bp.get("/import")
def import_form():
    return _render_import("", "")


@bp.post("/import")
def import_documents():
    batches_text = request.form.get("batches", "")
    sheets_text = request.form.get("sheets", "")
    try:
        parsed = parse_and_check(batches_text, sheets_text)
        new_id = repository.import_documents(
            get_db(), parsed.batches, parsed.sheets, note="网页导入"
        )
    except ImportError_ as exc:
        for message in exc.errors:
            flash(message, "error")
        return _render_import(batches_text, sheets_text, status=400)
    flash(
        f"已提交导入 #{new_id}：{len(parsed.batches)} 个批次、"
        f"{len(parsed.sheets)} 张汇总单。",
        "ok",
    )
    return redirect(url_for("main.index", submission=new_id))


@bp.post("/revision")
def update_revision():
    conn = get_db()
    code = request.form.get("code", "").strip()
    raw_revision = request.form.get("revision", "").strip()
    raw_base = request.form.get("base_id", "").strip()
    try:
        base_id = int(raw_base)
        revision = int(raw_revision)
    except ValueError:
        flash("修订号必须为正整数", "error")
        return redirect(url_for("main.index"))
    try:
        new_id = repository.update_revision(conn, base_id, code, revision)
    except repository.RevisionError as exc:
        flash(exc.message, "error")
        return redirect(url_for("main.index"))
    flash(f"已提交更版 #{new_id}：{code} 现行修订号改为 r{revision}。", "ok")
    return redirect(url_for("main.index"))

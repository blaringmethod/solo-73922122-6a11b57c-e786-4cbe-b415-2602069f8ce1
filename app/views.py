"""HTTP views for import, revision update, and reconciliation."""

from __future__ import annotations

import sqlite3

from flask import (
    Blueprint,
    current_app,
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
    latest, latest_snapshot = _current_state(conn)
    selected = request.args.getlist("batch")
    result = None
    error = None
    checked = None
    permalink = None
    # The batch list, sheet statuses and (when present) the conclusion all
    # come from this snapshot. For a ticked page it is the submission recorded
    # at tick time, never whatever happens to be newest at submit time.
    snapshot = latest_snapshot
    must_retick = False

    if request.args.get("check") == "1":
        if latest is None:
            error = "尚未导入批次修订清单与汇总单，无法核对。"
        elif not selected:
            error = "请至少勾选一个待验批次。"
        else:
            recorded = _parse_recorded_id(request.args.get("from"))
            if recorded is None:
                error = (
                    "缺少原提交信息：该核对链接未记录勾选时所见的提交，"
                    "无法判断是否仍适用。已按最新提交显示，请重新勾选后核对。"
                )
                must_retick = True
            else:
                error, result, checked, permalink, must_retick = _run_check(
                    conn, recorded, selected
                )
                if checked is not None:
                    snapshot = checked.snapshot
                    latest = checked.latest
                    if latest.id != snapshot.submission.id:
                        latest_snapshot = repository.load_snapshot(conn, latest.id)
                    else:
                        latest_snapshot = snapshot

    if must_retick:
        # Do not keep a selection that is known not to match what was ticked.
        selected = []
        # A commit may have landed while this request was running; show the
        # state the user must now re-tick from.
        latest, latest_snapshot = _current_state(conn)
        snapshot = latest_snapshot

    def render():
        return render_template(
            "index.html",
            latest=latest,
            latest_snapshot=latest_snapshot,
            snapshot=snapshot,
            form_from=snapshot.submission.id if snapshot else None,
            checked=checked,
            permalink=permalink,
            stale=stale_sheets_for(snapshot) if snapshot else (),
            submissions=repository.list_submissions(conn, limit=8),
            selected=selected,
            result=result,
            error=error,
        )

    html = render()
    if checked is None:
        return html

    # Rendering is the last step of the calculation too. Verify once more
    # after it: a commit that landed during it must not let a now-stale
    # conclusion reach the browser, and a harmless one should be reflected in
    # the "newest submission" label.
    try:
        verified = repository.verify_check_base(conn, checked)
    except repository.RevisionError as exc:
        latest, latest_snapshot = _current_state(conn)
        snapshot = latest_snapshot
        checked = None
        permalink = None
        result = None
        error = exc.message
        selected = []
    except sqlite3.Error:
        current_app.logger.exception("post-render check verification failed")
        latest, latest_snapshot = _current_state(conn)
        snapshot = latest_snapshot
        checked = None
        permalink = None
        result = None
        error = "核对完成时状态校验失败，本次不给出结论，请稍后重试。"
        selected = []
    else:
        if verified.latest.id != latest.id:
            # Only a harmless commit landed while rendering; the conclusion
            # stands, but the page should name the real newest submission.
            checked = verified
            latest = verified.latest
            latest_snapshot = repository.load_snapshot(conn, latest.id)
            return render()
        return html
    return render()


def _run_check(conn, recorded_id: int, selected: list[str]):
    """Reconcile one ticked selection; returns render context pieces.

    The conclusion is computed from the recorded snapshot only. Nothing is
    concluded when the ticked selection has been invalidated by commits that
    landed before or while the cover search ran.
    """
    try:
        base = repository.load_check_base(
            conn, recorded_id=recorded_id, selected_codes=selected
        )
    except LookupError as exc:
        return f"{exc}；请重新勾选后核对。", None, None, None, True
    except sqlite3.Error:
        current_app.logger.exception("check base load failed")
        return "读取核对基准时出错，本次不给出结论，请稍后重试。", None, None, None, True

    if base.is_stale:
        return _stale_message(base), None, None, None, True

    # Reconcile against the ticked snapshot, the same one the boxes were
    # rendered from.
    outcome = reconcile(base.snapshot, selected)
    try:
        # Commits may land while the cover search runs. Harmless ones (a
        # revision update of an unselected batch) keep the old selection
        # valid; one that touches the selection means no conclusion today.
        base = repository.verify_check_base(conn, base)
    except repository.RevisionError as exc:
        return exc.message, None, None, None, True
    permalink = url_for(
        "main.index",
        check=1,
        **{"from": base.snapshot.submission.id},
        batch=selected,
    )
    return None, outcome, base, permalink, False


def _parse_recorded_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _stale_message(base: repository.CheckBase) -> str:
    parts = ["勾选时所见的提交已过期，本次不出结论，请刷新后重新勾选核对。"]
    if base.reimported_ids:
        ids = "、".join(f"#{i}" for i in base.reimported_ids)
        parts.append(f"其后发生过整套重导（{ids}）。")
    if base.changed_batches:
        codes = "、".join(base.changed_batches)
        parts.append(
            f"所选批次的修订号在勾选后被改动过：{codes}（即使后来改回原值也算过期）。"
        )
    return "".join(parts)


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

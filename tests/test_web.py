import re

BATCHES = """B01 r2
B02 r2
B03 r1
B04 r1
"""

SHEETS = """S1 150
B01 r2
B02 r2

S2 90
B03 r1
B04 r1
"""


def do_import(client, batches=BATCHES, sheets=SHEETS):
    return client.post(
        "/import", data={"batches": batches, "sheets": sheets}, follow_redirects=True
    )


def check(client, batches, submission="1"):
    query = "check=1&from=" + str(submission) + "&" + "&".join(
        "batch=" + batch for batch in batches
    )
    return client.get("/?" + query)


def test_import_then_reconcile_ok(client):
    response = do_import(client)
    assert response.status_code == 200
    assert "已提交导入" in response.get_data(as_text=True)

    text = check(client, ["B01", "B02", "B03", "B04"]).get_data(as_text=True)
    assert 'data-status="ok"' in text
    assert "240" in text


def test_reconcile_conflict_renders_two_witnesses(client):
    do_import(
        client,
        sheets="S1 150\nB01 r2\nB02 r2\n\nS2 160\nB01 r2\nB02 r2\n",
    )
    text = check(client, ["B01", "B02"]).get_data(as_text=True)
    assert 'data-status="conflict"' in text
    assert "S1" in text and "S2" in text
    assert "合计 150 页" in text and "合计 160 页" in text


def test_no_cover_message(client):
    do_import(client, sheets="S1 150\nB01 r2\n")
    text = check(client, ["B01", "B02"]).get_data(as_text=True)
    assert 'data-status="none"' in text
    assert "无法出数" in text


def test_revision_update_makes_sheet_stale(client):
    do_import(client)
    text = client.get("/").get_data(as_text=True)
    assert "r2" in text

    response = client.post(
        "/revision", data={"base_id": "1", "code": "B01", "revision": "3"},
        follow_redirects=True,
    )
    text = response.get_data(as_text=True)
    assert "已提交更版" in text
    assert "B01 单上修订 r2 ≠ 现行 r3" in text

    text = check(client, ["B01", "B02"], submission="2").get_data(as_text=True)
    assert 'data-status="none"' in text


def test_revision_update_rejects_stale_base_id(client):
    do_import(client)
    client.post("/revision", data={"base_id": "1", "code": "B01", "revision": "3"})
    text = client.post(
        "/revision", data={"base_id": "1", "code": "B02", "revision": "3"},
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "已不是最新提交" in text


def test_import_validation_keeps_previous_state(client):
    do_import(client)
    response = do_import(client, batches="B01 r1\nB01 r2\n")
    assert response.status_code == 400
    assert "重复" in response.get_data(as_text=True)
    # The original submission is still the current one.
    text = client.get("/").get_data(as_text=True)
    assert "最新提交 <strong id=\"latest-id\">#1</strong>" in text


def test_empty_database_shows_import_prompt(client):
    text = client.get("/").get_data(as_text=True)
    assert "还没有已提交的批次修订清单" in text


def test_check_without_selection_warns(client):
    do_import(client)
    text = client.get("/?check=1&from=1").get_data(as_text=True)
    assert "请至少勾选一个待验批次" in text


def test_check_without_recorded_submission_asks_to_retick(client):
    do_import(client)
    text = client.get("/?check=1&batch=B01&batch=B02").get_data(as_text=True)
    assert "缺少原提交信息" in text
    assert "重新勾选" in text
    assert "data-status=" not in text
    # The checkbox list falls back to the latest submission.
    assert 'name="from" value="1"' in text


def test_check_with_unknown_recorded_submission_asks_to_retick(client):
    do_import(client)
    text = check(client, ["B01", "B02"], submission="99").get_data(as_text=True)
    assert "重新勾选" in text
    assert "data-status=" not in text


def test_same_content_reimport_invalidates_old_page(client):
    """The reported bug: re-import in another window, same batch/revs.

    Submitting the old page must not reconcile the new documents.
    """
    do_import(client)
    # Same batch numbers and revisions, but a *different* committed state.
    do_import(client)
    text = check(client, ["B01", "B02", "B03", "B04"], submission="1").get_data(
        as_text=True
    )
    assert "data-status=" not in text
    assert "整套重导" in text
    assert "重新勾选" in text
    # New documents must not be reported from the old page.
    assert "240" not in text


def test_reimport_with_different_numbers_never_leaks_new_totals(client):
    do_import(client)
    do_import(
        client,
        sheets="S1 999\nB01 r2\nB02 r2\n\nS2 1\nB03 r1\nB04 r1\n",
    )
    text = check(client, ["B01", "B02", "B03", "B04"], submission="1").get_data(
        as_text=True
    )
    assert "data-status=" not in text
    assert "核对结果" not in text
    assert "整套重导" in text


def test_selected_revision_change_invalidates_even_if_reverted(client):
    do_import(client)
    # B01: r2 -> r3 -> r2. The old page still saw r2, but the committed state
    # changed underneath it, so it must not be trusted.
    client.post("/revision", data={"base_id": "1", "code": "B01", "revision": "3"})
    client.post("/revision", data={"base_id": "2", "code": "B01", "revision": "2"})
    text = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert "data-status=" not in text
    assert "B01" in text
    assert "改回原值也算过期" in text
    assert "重新勾选" in text


def test_unselected_revision_change_keeps_old_page_valid(client):
    do_import(client)
    # B03/B04 are not ticked; changing B03 must not invalidate B01+B02.
    client.post("/revision", data={"base_id": "1", "code": "B03", "revision": "2"})
    text = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert 'data-status="ok"' in text
    assert "150" in text
    # The conclusion names the ticked submission, not the newest one.
    assert 'id="checked-id">#1</span>' in text
    assert 'id="latest-id">#2</strong>' in text
    assert 'data-submission="1"' in text
    assert "只改动了<em>未勾选</em>的批次" in text


def test_page_labels_all_sections_with_the_checked_submission(client):
    do_import(client)
    text = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert "本次核对所用提交" in text
    assert "现行批次与修订号（提交 #1）" in text
    assert "汇总单与修订吻合情况（提交 #1）" in text
    assert "核对结果 <span" in text and "依据提交 #1" in text


def test_commit_during_calculation_that_invalidates_shows_no_conclusion(
    client, app, monkeypatch
):
    do_import(client)
    from app import repository, views

    real_reconcile = views.reconcile

    def race(snapshot, selected):
        from app.db import connect

        conn = connect(app.config["DATABASE"])
        try:
            repository.update_revision(conn, 1, "B01", 3)
        finally:
            conn.close()
        return real_reconcile(snapshot, selected)

    monkeypatch.setattr(views, "reconcile", race)
    text = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert "核对期间" in text
    assert "不给出结论" in text
    assert "data-status=" not in text
    assert 'id="checked-id"' not in text


def test_commit_during_calculation_of_unselected_batch_still_concludes(
    client, app, monkeypatch
):
    do_import(client)
    from app import repository, views

    real_reconcile = views.reconcile

    def race(snapshot, selected):
        from app.db import connect

        conn = connect(app.config["DATABASE"])
        try:
            repository.update_revision(conn, 1, "B03", 2)
        finally:
            conn.close()
        return real_reconcile(snapshot, selected)

    monkeypatch.setattr(views, "reconcile", race)
    text = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert 'data-status="ok"' in text
    assert "150" in text
    assert 'id="checked-id">#1</span>' in text
    assert 'id="latest-id">#2</strong>' in text


def test_commit_during_rendering_that_invalidates_shows_no_conclusion(
    client, app, monkeypatch
):
    do_import(client)
    from app import repository, views

    real_render = views.render_template
    state = {"fired": False}

    def render_with_race(template, **context):
        html = real_render(template, **context)
        if not state["fired"] and context.get("checked") is not None:
            state["fired"] = True
            from app.db import connect

            conn = connect(app.config["DATABASE"])
            try:
                repository.update_revision(conn, 1, "B01", 3)
            finally:
                conn.close()
        return html

    monkeypatch.setattr(views, "render_template", render_with_race)
    text = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert state["fired"]
    assert "核对期间" in text
    assert "data-status=" not in text


def test_commit_during_rendering_of_unselected_batch_updates_latest_label(
    client, app, monkeypatch
):
    do_import(client)
    from app import repository, views

    real_render = views.render_template
    state = {"fired": False}

    def render_with_race(template, **context):
        html = real_render(template, **context)
        if not state["fired"] and context.get("checked") is not None:
            state["fired"] = True
            from app.db import connect

            conn = connect(app.config["DATABASE"])
            try:
                repository.update_revision(conn, 1, "B03", 2)
            finally:
                conn.close()
        return html

    monkeypatch.setattr(views, "render_template", render_with_race)
    text = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert 'data-status="ok"' in text
    assert "150" in text
    assert 'id="checked-id">#1</span>' in text
    assert 'id="latest-id">#2</strong>' in text
    assert "只改动了<em>未勾选</em>的批次" in text


def test_permalink_replays_same_conclusion(client):
    do_import(client)
    client.post("/revision", data={"base_id": "1", "code": "B03", "revision": "2"})
    html = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    match = re.search(r'id="check-permalink" href="([^"]+)"', html)
    assert match
    again = client.get(match.group(1).replace("&amp;", "&")).get_data(as_text=True)
    assert 'data-status="ok"' in again
    assert 'id="checked-id">#1</span>' in again
    assert 'id="latest-id">#2</strong>' in again


def test_checkbox_form_posts_the_snapshot_it_displayed(client):
    do_import(client)
    client.post("/revision", data={"base_id": "1", "code": "B03", "revision": "2"})
    # Re-checking from a result page keeps that page's submission, even though
    # #2 is newest and only touched an unselected batch.
    html = check(client, ["B01", "B02"], submission="1").get_data(as_text=True)
    assert 'name="from" value="1"' in html
    # The visible list is the ticked snapshot, not the newest one.
    assert 'id="checked-id">#1</span>' in html
    assert "B03</td>" in html and "r1" in html
    # A plain visit shows the newest submission and posts from #2.
    latest_page = client.get("/").get_data(as_text=True)
    assert 'name="from" value="2"' in latest_page
    assert "r2" in latest_page
    assert 'name="from" value="2"' in latest_page

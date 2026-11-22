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


def test_import_then_reconcile_ok(client):
    response = do_import(client)
    assert response.status_code == 200
    assert "已提交导入" in response.get_data(as_text=True)

    response = client.get("/?check=1&batch=B01&batch=B02&batch=B03&batch=B04")
    text = response.get_data(as_text=True)
    assert 'data-status="ok"' in text
    assert "240" in text


def test_reconcile_conflict_renders_two_witnesses(client):
    do_import(
        client,
        sheets="S1 150\nB01 r2\nB02 r2\n\nS2 160\nB01 r2\nB02 r2\n",
    )
    text = client.get("/?check=1&batch=B01&batch=B02").get_data(as_text=True)
    assert 'data-status="conflict"' in text
    assert "S1" in text and "S2" in text
    assert "合计 150 页" in text and "合计 160 页" in text


def test_no_cover_message(client):
    do_import(client, sheets="S1 150\nB01 r2\n")
    text = client.get("/?check=1&batch=B01&batch=B02").get_data(as_text=True)
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

    text = client.get("/?check=1&batch=B01&batch=B02").get_data(as_text=True)
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
    assert "当前提交 <strong>#1</strong>" in text


def test_empty_database_shows_import_prompt(client):
    text = client.get("/").get_data(as_text=True)
    assert "还没有已提交的批次修订清单" in text


def test_check_without_selection_warns(client):
    do_import(client)
    text = client.get("/?check=1").get_data(as_text=True)
    assert "请至少勾选一个待验批次" in text

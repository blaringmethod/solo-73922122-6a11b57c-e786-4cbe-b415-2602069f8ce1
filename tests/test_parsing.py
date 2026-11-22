import pytest

from app.parsing import ImportError_, parse_and_check, parse_batch_list, parse_sheets


def test_parse_batch_list_tolerates_labels_and_prefixes():
    batches = parse_batch_list(
        "# comment\n批次 修订号\nB01 r2\nB02, rev3\nB03 修订=1\n"
    )
    assert [(b.code, b.revision) for b in batches] == [
        ("B01", 2),
        ("B02", 3),
        ("B03", 1),
    ]


def test_parse_sheets_with_blank_separators():
    sheets = parse_sheets(
        "S1 150\nB01 r2\nB02 r2\n\nS2, 90\nB03 1\n"
    )
    assert [(s.code, s.total_pages) for s in sheets] == [("S1", 150), ("S2", 90)]
    assert sheets[0].entries == (("B01", 2), ("B02", 2))


def test_sheets_without_blank_separator_are_caught_by_cross_check():
    # Without blank lines "S2 90" is indistinguishable from an entry, so
    # parsing alone cannot reject it. The batch cross-check must catch it.
    with pytest.raises(ImportError_) as info:
        parse_and_check("B01 r2\nB02 r2\nB03 r1\n",
                        "S1 150\nB01 r2\nB02 r2\nS2 90\nB03 r1\n")
    messages = " ".join(info.value.errors)
    assert "S2" in messages and "不存在" in messages


def test_sheet_needs_blank_line_separator_documented_behaviour():
    # A well-formed single sheet without a trailing blank line still parses.
    sheets = parse_sheets("S1 150\nB01 r2\nB02 r2")
    assert [(s.code, s.total_pages) for s in sheets] == [("S1", 150)]


def test_rejects_duplicate_batches_and_sheets():
    with pytest.raises(ImportError_) as info:
        parse_batch_list("B01 r1\nB01 r2\n")
    assert any("重复" in message for message in info.value.errors)

    with pytest.raises(ImportError_) as info:
        parse_sheets("S1 10\nB01 r1\n\nS1 20\nB01 r1\n")
    assert any("重复" in message for message in info.value.errors)


def test_rejects_sheet_without_entries_and_unknown_batch():
    with pytest.raises(ImportError_) as info:
        parse_and_check("B01 r1\n", "S1 10\n")
    assert any("没有所含批次" in message for message in info.value.errors)

    with pytest.raises(ImportError_) as info:
        parse_and_check("B01 r1\n", "S1 10\nB99 r1\n")
    assert any("B99" in message for message in info.value.errors)


def test_rejects_limits_and_bad_numbers():
    too_many = "\n".join(f"B{i:02d} r1" for i in range(17))
    with pytest.raises(ImportError_) as info:
        parse_batch_list(too_many)
    assert any("超过上限" in message for message in info.value.errors)

    with pytest.raises(ImportError_) as info:
        parse_batch_list("B01 r0\nB02 rx\n")
    assert any("正整数" in message for message in info.value.errors)
    assert any("无法解析" in message for message in info.value.errors)

"""Parsing and validation for the two imported documents.

Batch revision list (one batch per line)::

    B01 r2
    B02, rev3
    B03 修订=1

Summary sheets (blank line separates sheets; first line of each block is the
sheet header ``code total_pages``; remaining lines are ``batch revision``)::

    S-01 120
    B01 r2
    B02 r3

    S-02, 95
    B01 r2
    B03 3

Comments are whole lines starting with ``#``. Both labels and ``r``/``rev``
prefixes are tolerated so hand-typed and CSV-like input both work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Batch, Sheet

MAX_BATCHES = 16
MAX_SHEETS = 40

_SPLIT_RE = re.compile(r"[,，;；\t ]+")
_HEADER_WORDS = {"批次", "batch", "batches", "code", "编号", "批次号"}
_REVISION_LABELS = {"r", "rev", "revision", "修订", "修订号", "版本", "版次"}
_PAGE_LABELS = {"页数", "总页数", "pages", "page", "total", "totalpages"}
_BRACKETS = "[]【】()（）"


def _normalise(line: str) -> str:
    return line.replace("＝", "=")


class ImportError_(ValueError):
    """Raised with a list of human-readable problems; nothing is stored."""

    def __init__(self, errors: list[str]):
        super().__init__("；".join(errors))
        self.errors = tuple(errors)


@dataclass(frozen=True)
class Parsed:
    batches: tuple[Batch, ...]
    sheets: tuple[Sheet, ...]


def _clean_lines(text: str, keep_blanks: bool = False) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            if keep_blanks:
                lines.append("")
            continue
        lines.append(line)
    return lines


def _tokens(line: str) -> list[str]:
    return [part for part in _SPLIT_RE.split(_normalise(line)) if part]


def _parse_int(token: str, label: str, where: str, errors: list[str]) -> int | None:
    value = token.strip()
    if "=" in value:
        value = value.split("=", 1)[1]
    lowered = value.casefold()
    for prefix in ("revision", "rev", "r"):
        if lowered.startswith(prefix) and lowered != prefix:
            value = value[len(prefix):]
            break
    value = value.rstrip("页号")
    try:
        number = int(value)
    except ValueError:
        errors.append(f"{where}：无法解析{label}“{token}”")
        return None
    if number <= 0:
        errors.append(f"{where}：{label}必须为正整数，得到“{token}”")
        return None
    return number


def _strip_code(token: str) -> str:
    if "=" in token:
        token = token.split("=", 1)[0]
    return token.strip().strip(_BRACKETS)


def _parse_row(line: str, where: str, errors: list[str]) -> tuple[str, int] | None:
    tokens = _tokens(line)
    if not tokens:
        return None
    code = _strip_code(tokens[0])
    rest = [
        token
        for token in tokens[1:]
        if token.strip().casefold() not in _REVISION_LABELS
    ]
    if not code or not rest:
        errors.append(f"{where}：应为“批次 修订号”，得到“{line}”")
        return None
    if len(rest) > 1:
        errors.append(f"{where}：批次“{code}”后有多余内容“{' '.join(rest[1:])}”")
        return None
    revision = _parse_int(rest[0], "修订号", where, errors)
    if revision is None:
        return None
    return code, revision


def parse_batch_list(text: str) -> tuple[Batch, ...]:
    lines = _clean_lines(text)
    errors: list[str] = []
    if not lines:
        raise ImportError_(["批次修订清单为空"])

    first_tokens = {token.casefold() for token in _tokens(lines[0])}
    if first_tokens and first_tokens <= _HEADER_WORDS | _REVISION_LABELS:
        lines = lines[1:]
        if not lines:
            raise ImportError_(["批次修订清单只有表头，没有批次"])

    batches: list[Batch] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        parsed = _parse_row(line, f"批次清单第 {number} 条", errors)
        if parsed is None:
            continue
        code, revision = parsed
        if code in seen:
            errors.append(f"批次清单第 {number} 条：批次“{code}”重复")
            continue
        seen.add(code)
        batches.append(Batch(code, revision))

    if len(batches) > MAX_BATCHES:
        errors.append(f"批次数 {len(batches)} 超过上限 {MAX_BATCHES}")
    if not batches and not errors:
        errors.append("批次修订清单没有可用条目")
    if errors:
        raise ImportError_(errors)
    return tuple(batches)


def _parse_sheet_header(
    line: str, where: str, errors: list[str]
) -> tuple[str, int] | None:
    tokens = _tokens(line)
    tokens = [
        token for token in tokens if token.strip().casefold() not in _PAGE_LABELS
    ]
    if len(tokens) != 2:
        errors.append(f"{where}：单头应为“单号 总页数”，得到“{line}”")
        return None
    code = _strip_code(tokens[0])
    if not code:
        errors.append(f"{where}：单号为空")
        return None
    pages = _parse_int(tokens[1], "总页数", where, errors)
    if pages is None:
        return None
    return code, pages


def parse_sheets(text: str) -> tuple[Sheet, ...]:
    raw_lines = _clean_lines(text, keep_blanks=True)
    errors: list[str] = []
    if not any(raw_lines):
        raise ImportError_(["汇总单为空"])

    trimmed = list(raw_lines)
    while trimmed and trimmed[-1] == "":
        trimmed.pop()
    while trimmed and trimmed[0] == "":
        trimmed.pop(0)

    blocks: list[list[str]] = [[]]
    for line in trimmed:
        if not line:
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line)
    blocks = [block for block in blocks if block]
    if not blocks:
        raise ImportError_(["汇总单为空"])

    sheets: list[Sheet] = []
    seen_codes: set[str] = set()
    for index, block in enumerate(blocks, start=1):
        where = f"汇总单第 {index} 张"
        header = _parse_sheet_header(block[0], where, errors)
        if header is None:
            continue
        code, pages = header
        if code in seen_codes:
            errors.append(f"{where}：单号“{code}”重复")
            continue
        seen_codes.add(code)
        entries: list[tuple[str, int]] = []
        entry_codes: set[str] = set()
        for row_number, line in enumerate(block[1:], start=1):
            parsed = _parse_row(line, f"{where} 第 {row_number} 行", errors)
            if parsed is None:
                continue
            batch_code, revision = parsed
            if batch_code in entry_codes:
                errors.append(f"{where}：批次“{batch_code}”在同一张单内重复")
                continue
            entry_codes.add(batch_code)
            entries.append((batch_code, revision))
        if not entries:
            errors.append(f"{where}（{code}）：没有所含批次")
            continue
        if len(entries) > MAX_BATCHES:
            errors.append(
                f"{where}（{code}）：所含批次数 {len(entries)} 超过上限 {MAX_BATCHES}"
            )
            continue
        sheets.append(Sheet(code, pages, tuple(sorted(entries))))

    if len(sheets) > MAX_SHEETS:
        errors.append(f"汇总单张数 {len(sheets)} 超过上限 {MAX_SHEETS}")
    if not sheets and not errors:
        errors.append("汇总单没有可用单据")
    if errors:
        raise ImportError_(errors)
    return tuple(sheets)


def parse_and_check(batch_text: str, sheet_text: str) -> Parsed:
    errors: list[str] = []
    try:
        batches = parse_batch_list(batch_text)
    except ImportError_ as exc:
        errors.extend(exc.errors)
        batches = ()
    try:
        sheets = parse_sheets(sheet_text)
    except ImportError_ as exc:
        errors.extend(exc.errors)
        sheets = ()

    if errors:
        raise ImportError_(errors)

    known = {batch.code for batch in batches}
    for sheet in sheets:
        unknown = [code for code in sheet.batches if code not in known]
        if unknown:
            errors.append(
                f"汇总单“{sheet.code}”含批次清单中不存在的批次：{'、'.join(unknown)}"
            )
    if errors:
        raise ImportError_(errors)
    return Parsed(tuple(batches), tuple(sheets))

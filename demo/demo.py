#!/usr/bin/env python3
"""End-to-end demo against a running server (Compose or local gunicorn).

Usage: python3 demo/demo.py [base_url] [output_dir]

The script only speaks HTTP; it never touches the SQLite file directly, so it
exercises exactly what a reviewer sees in the browser. It writes a Markdown
transcript plus the raw HTML of key steps for evidence.

The main scenario is the stale-page bug: an old check page must never reconcile
documents committed after its boxes were ticked.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "demo/out")

TRANSCRIPT: list[str] = []
FAILURES: list[str] = []


def say(message: str = "") -> None:
    print(message)
    TRANSCRIPT.append(message)


def expect(condition: bool, message: str) -> bool:
    say(f"- {'通过' if condition else '未达预期'}：{message}")
    if not condition:
        FAILURES.append(message)
    return condition


class _StatusParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.status = None
        self.total = None
        self.latest_id = None
        self.checked_id = None
        self.conflict_sheets: list[str] = []
        self.conflict_totals: list[str] = []
        self._capture = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "result":
            self.status = attrs.get("data-status")
        if attrs.get("id") == "page-total":
            self._capture = "total"
        if attrs.get("id") == "latest-id":
            self._capture = "latest-id"
        if attrs.get("id") == "checked-id":
            self._capture = "checked-id"
        if tag == "div" and "witness-sheets" in attrs.get("class", ""):
            self._capture = "witness"
        if tag == "div" and "witness-total" in attrs.get("class", ""):
            self._capture = "witness-total"

    def handle_endtag(self, tag):
        if self._capture and tag in ("p", "div", "span", "strong"):
            self._capture = None

    def handle_data(self, data):
        if self._capture == "total":
            match = re.search(r"\d+", data)
            if match:
                self.total = int(match.group())
        elif self._capture == "latest-id":
            match = re.search(r"#(\d+)", data)
            if match:
                self.latest_id = int(match.group(1))
        elif self._capture == "checked-id":
            match = re.search(r"#(\d+)", data)
            if match:
                self.checked_id = int(match.group(1))
        elif self._capture == "witness":
            text = data.strip()
            if text:
                self.conflict_sheets.append(text)
        elif self._capture == "witness-total":
            text = data.strip()
            if text:
                self.conflict_totals.append(text)


def request(path: str, data: dict | None = None):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, response.read().decode(), response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), url


def parse(html: str) -> _StatusParser:
    parser = _StatusParser()
    parser.feed(html)
    return parser


def flash_messages(html: str) -> list[str]:
    return [
        re.sub(r"<[^>]+>", "", m).strip()
        for m in re.findall(r'<li class="flash [^"]+">(.*?)</li>', html, re.S)
    ]


def save(name: str, html: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(html, encoding="utf-8")


def latest_id(html: str) -> int | None:
    parsed = parse(html)
    if parsed.latest_id is not None:
        return parsed.latest_id
    match = re.search(r'name="base_id" value="(\d+)"', html)
    return int(match.group(1)) if match else None


def check_url(selection, submission: int | None) -> str:
    pairs: list[tuple[str, str]] = [("check", "1")]
    if submission is not None:
        pairs.append(("from", str(submission)))
    pairs.extend(("batch", code) for code in selection)
    return "/?" + urllib.parse.urlencode(pairs)


def do_import(batches: str, sheets: str) -> tuple[int, str, int | None]:
    status, html, url = request("/import", {"batches": batches, "sheets": sheets})
    save("import.html", html)
    say(f"- `POST /import` → HTTP {status}，落点 `{url}`")
    for message in flash_messages(html):
        say(f"- 页面提示：{message}")
    return status, html, latest_id(html)


def do_revision(base_id: int, code: str, revision: int) -> tuple[int, str, int | None]:
    status, html, url = request(
        "/revision", {"base_id": str(base_id), "code": code, "revision": str(revision)}
    )
    save("revision.html", html)
    say(f"- `POST /revision`（{code} → r{revision}，base #{base_id}）→ HTTP {status}")
    for message in flash_messages(html):
        say(f"- 页面提示：{message}")
    return status, html, latest_id(html)


BATCHES = "B01 r2\nB02 r2\nB03 r1\nB04 r1\n"
SHEETS = "S1 150\nB01 r2\nB02 r2\n\nS2 90\nB03 r1\nB04 r1\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    say("# 复扫页数核对 · 联机演示记录")
    say()
    say(f"服务地址：`{BASE}`")
    say()

    status, _, _ = request("/healthz")
    say(f"- 健康检查 `GET /healthz` → HTTP {status}")
    if status != 200:
        say("\n服务未就绪，演示终止。")
        return 1

    say("")
    say("## 第 1 步：导入初始清单与汇总单")
    say()
    status, _, first = do_import(BATCHES, SHEETS)
    if status != 200 or first is None:
        say("\n导入失败，演示终止。")
        return 2
    say(f"- 初始提交编号：# {first}")

    say("")
    say("## 第 2 步：在初始提交上勾选 B01+B02 得到报数")
    say()
    initial_url = check_url(["B01", "B02"], first)
    status, html, _ = request(initial_url)
    save("01-check-ok.html", html)
    result = parse(html)
    expect(status == 200, "`GET " + initial_url + "` 返回 HTTP 200")
    expect(result.status == "ok" and result.total == 150, "报数 150 页（S1），状态 ok")
    expect(result.checked_id == first, f"页面标明核对所用提交 #{first}")
    expect(f'data-submission="{first}"' in html, "批次清单与单据状态均标为同一提交")
    say(f"- 本次结论链接：`{initial_url}`")

    say("")
    say("## 第 3 步（缺陷复现）：另一窗口整套重导，批次号与修订号完全相同")
    say()
    say("旧页面仍是第 2 步的链接；在另一窗口用相同批次号和修订号重导：")
    status, _, second = do_import(BATCHES, SHEETS)
    expect(second is not None and second != first, f"重导产生新提交 #{second}")
    status, html, _ = request(initial_url)
    save("02-stale-after-reimport.html", html)
    result = parse(html)
    expect(status == 200, "旧链接仍可访问（HTTP 200）")
    expect(result.status is None, "旧链接不再给出核对结论（无 result 块）")
    expect("整套重导" in html and "重新勾选" in html, "提示整套重导、要求重新勾选")
    expect("150" not in html.split("核对结果")[0] or "核对结果" not in html,
           "未用新单据为旧页面报数")

    say("")
    say("## 第 4 步：从最新提交重新勾选，仍得到 150 页")
    say()
    fresh_url = check_url(["B01", "B02"], second)
    status, html, _ = request(fresh_url)
    save("03-recheck-ok.html", html)
    result = parse(html)
    expect(result.status == "ok" and result.total == 150, "新链接状态 ok、报数 150 页")
    expect(result.checked_id == second, f"页面标明核对所用提交 #{second}")

    say("")
    say("## 第 5 步：所选批次改版后又改回原值，旧勾选仍须过期")
    say()
    status, _, r3 = do_revision(second, "B01", 3)
    if status != 200 or r3 is None:
        say("- 更版失败，演示终止。")
        return 2
    status, _, back = do_revision(r3, "B01", 2)
    if status != 200 or back is None:
        say("- 改回失败，演示终止。")
        return 2
    expect(back != r3, f"B01 经 r3 后改回 r2，产生提交 #{back}")
    status, html, _ = request(fresh_url)
    save("04-stale-after-revert.html", html)
    result = parse(html)
    expect(result.status is None, "修订号即使改回 r2，旧勾选也不出结论")
    expect("B01" in html and "算过期" in html, "点名 B01 并说明改回原值也算过期")
    expect("重新勾选" in html, "提示重新勾选")

    say("")
    say("## 第 6 步：只更版未勾选批次，原勾选仍可核对")
    say()
    settled_url = check_url(["B01", "B02"], back)
    status, html, _ = request(settled_url)
    result = parse(html)
    expect(result.status == "ok" and result.total == 150, "改回后新链接可正常报数 150 页")
    status, _, b03 = do_revision(back, "B03", 2)
    if status != 200 or b03 is None:
        say("- 更版失败，演示终止。")
        return 2
    status, html, _ = request(settled_url)
    save("05-unselected-change-ok.html", html)
    result = parse(html)
    expect(result.status == "ok" and result.total == 150, "仅 B03 改版后，B01+B02 旧勾选仍报 150 页")
    expect(result.checked_id == back, f"结论仍以勾选时提交 #{back} 为准")
    expect(result.latest_id == b03, f"页面同时标明最新提交 #{b03}")
    expect("未勾选" in html, "说明只改动了未勾选批次")
    expect("S1" in html and "可用" in html, "单据状态取自勾选时提交（S1 可用）")

    say("")
    say("## 第 7 步：缺少原提交信息的旧链接")
    say()
    status, html, _ = request("/?check=1&batch=B01&batch=B02")
    save("06-missing-from.html", html)
    result = parse(html)
    expect(result.status is None, "不带 from 的链接不给出结论")
    expect("缺少原提交信息" in html and "重新勾选" in html, "提示缺少原提交信息并要求重新勾选")

    say("")
    say("## 第 8 步：重新导入后正常核对并报分歧")
    say()
    sheets_v2 = "T1 150\nB01 r3\nB02 r2\n\nT2 90\nB03 r2\nB04 r1\n"
    status, _, latest = do_import("B01 r3\nB02 r2\nB03 r2\nB04 r1\n", sheets_v2)
    if status != 200 or latest is None:
        say("- 重导失败，演示终止。")
        return 2
    conflict_url = check_url(["B01", "B02"], latest)
    status, html, _ = request(conflict_url)
    result = parse(html)
    expect(result.status == "ok" and result.total == 150, "T1 唯一覆盖，先报 150 页")

    sheets_conflict = sheets_v2 + "\nT3 170\nB01 r3\nB02 r2\n"
    status, _, latest2 = do_import("B01 r3\nB02 r2\nB03 r2\nB04 r1\n", sheets_conflict)
    if status != 200 or latest2 is None:
        say("- 重导失败，演示终止。")
        return 2
    status, html, _ = request(conflict_url)
    save("07-stale-after-second-reimport.html", html)
    result = parse(html)
    expect(result.status is None, "第二次重导后，先前链接不出结论")

    conflict_url = check_url(["B01", "B02"], latest2)
    status, html, _ = request(conflict_url)
    save("08-check-conflict.html", html)
    result = parse(html)
    expect(result.status == "conflict", "从最新提交重新勾选后报分歧")
    expect(
        len(result.conflict_sheets) >= 2 and len(set(result.conflict_totals)) >= 2,
        "分歧列出两组单号与不同合计",
    )
    for sheet_text, total_text in zip(result.conflict_sheets, result.conflict_totals):
        say(f"- 分歧：{sheet_text} → {total_text}")

    say("")
    say("## 结论")
    say()
    say(f"- 自动判定：{'全部通过' if not FAILURES else '存在未达预期'}")
    if FAILURES:
        for failure in FAILURES:
            say(f"  - 未达预期：{failure}")
    say(f"- 原始响应保存在 `{OUT}/`。")

    (OUT / "transcript.md").write_text("\n".join(TRANSCRIPT) + "\n", encoding="utf-8")
    return 0 if not FAILURES else 2


if __name__ == "__main__":
    raise SystemExit(main())

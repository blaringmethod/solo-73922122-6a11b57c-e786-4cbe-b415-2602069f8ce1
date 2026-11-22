#!/usr/bin/env python3
"""End-to-end demo against a running server (Compose or local gunicorn).

Usage: python3 demo/demo.py [base_url] [output_dir]

The script only speaks HTTP; it never touches the SQLite file directly, so it
exercises exactly what a reviewer sees in the browser. It writes a Markdown
transcript plus the raw HTML of key steps for evidence.
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


def say(message: str = "") -> None:
    print(message)
    TRANSCRIPT.append(message)


class _StatusParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.status = None
        self.total = None
        self.latest_id = None
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
            self._latest = True
        if tag == "div" and "witness-sheets" in attrs.get("class", ""):
            self._capture = "witness"
        if tag == "div" and "witness-total" in attrs.get("class", ""):
            self._capture = "witness-total"

    def handle_endtag(self, tag):
        if self._capture and tag in ("p", "div"):
            self._capture = None

    def handle_data(self, data):
        if self._capture == "total":
            match = re.search(r"\d+", data)
            if match:
                self.total = int(match.group())
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
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode(), response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), url


def parse(html: str) -> _StatusParser:
    parser = _StatusParser()
    parser.feed(html)
    return parser


def flash_messages(html: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r'<li class="flash [^"]+">(.*?)</li>', html, re.S)]


def save(name: str, html: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(html, encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    say("# 复扫页数核对 · 联机演示记录")
    say()
    say(f"服务地址：`{BASE}`")
    say()

    status, html, _ = request("/healthz")
    say(f"- 健康检查 `GET /healthz` → HTTP {status}")
    if status != 200:
        say("\n服务未就绪，演示终止。")
        return 1

    say("")
    say("## 第 1 步：导入批次清单与汇总单")
    say()
    batches = "B01 r2\nB02 r2\nB03 r1\nB04 r1\n"
    sheets = (
        "S1 150\nB01 r2\nB02 r2\n\n"
        "S2 90\nB03 r1\nB04 r1\n"
    )
    status, html, url = request(
        "/import", {"batches": batches, "sheets": sheets}
    )
    save("01-import.html", html)
    say(f"- `POST /import` → HTTP {status}，落点 `{url}`")
    for message in flash_messages(html):
        say(f"- 页面提示：{message}")
    say()
    say("清单：")
    say("```")
    say(batches.rstrip())
    say("```")
    say("汇总单：")
    say("```")
    say(sheets.rstrip())
    say("```")

    say("")
    say("## 第 2 步：勾选全部四个批次核对")
    query = urllib.parse.urlencode([("check", "1")] + [("batch", c) for c in ("B01", "B02", "B03", "B04")])
    status, html, _ = request("/?" + query)
    save("02-check-ok.html", html)
    result = parse(html)
    say(f"- `GET /?{query}` → HTTP {status}，结果状态 `{result.status}`，报数 {result.total} 页")
    say("- 期望：S1+S2 唯一有效组合，150+90=240。")

    say("")
    say("## 第 3 步：更新 B01 现行修订号为 r3（旧汇总单失效）")
    say()
    base_id = re.search(r'name="base_id" value="(\d+)"', html)
    if not base_id:
        say("- 未能在页面找到当前提交编号，演示终止。")
        return 2
    status, html, url = request(
        "/revision", {"base_id": base_id.group(1), "code": "B01", "revision": "3"}
    )
    save("03-revision.html", html)
    say(f"- `POST /revision` → HTTP {status}，落点 `{url}`")
    for message in flash_messages(html):
        say(f"- 页面提示：{message}")
    stale_lines = re.findall(r'<li>([^<]*单上修订[^<]*)</li>', html)
    for line in stale_lines:
        say(f"- 不采用理由：{line.strip()}")

    status, html, _ = request("/?" + query)
    save("04-check-after-revision.html", html)
    result = parse(html)
    say(f"- 再次核对四个批次 → 结果状态 `{result.status}`")
    say("- 期望：S1 含 B01 r2 已不采用；B01 无可用单覆盖，因此无法出数（S2 仍可用但只覆盖 B03/B04）。")

    say("")
    say("## 第 4 步：换版后重新导入吻合的汇总单，并制造分歧")
    say()
    sheets_v2 = (
        "T1 150\nB01 r3\nB02 r2\n\n"
        "T2 90\nB03 r1\nB04 r1\n"
    )
    status, html, url = request("/import", {"batches": "B01 r3\nB02 r2\nB03 r1\nB04 r1\n", "sheets": sheets_v2})
    save("05-reimport.html", html)
    say(f"- `POST /import`（清单 r3 + 新单 T1/T2）→ HTTP {status}")
    for message in flash_messages(html):
        say(f"- 页面提示：{message}")

    query = urllib.parse.urlencode([("check", "1"), ("batch", "B01"), ("batch", "B02")])
    status, html, _ = request("/?" + query)
    result = parse(html)
    say(f"- 核对 B01+B02 → 状态 `{result.status}`，报数 {result.total} 页（T1=150）")

    say("")
    say("再导入一张与 T1 覆盖相同、合计不同的 T3＝170，制造汇总分歧：")
    sheets_conflict = sheets_v2 + "\nT3 170\nB01 r3\nB02 r2\n"
    status, html, url = request("/import", {"batches": "B01 r3\nB02 r2\nB03 r1\nB04 r1\n", "sheets": sheets_conflict})
    save("06-import-conflict.html", html)
    say(f"- `POST /import` → HTTP {status}")
    for message in flash_messages(html):
        say(f"- 页面提示：{message}")

    status, html, _ = request("/?" + query)
    save("07-check-conflict.html", html)
    result = parse(html)
    say(f"- 核对 B01+B02 → 状态 `{result.status}`")
    for sheet_text, total_text in zip(result.conflict_sheets, result.conflict_totals):
        say(f"- 分歧：{sheet_text} → {total_text}")
    say("- 期望：T1（150）与 T3（170）是两组不同的有效组合，页面应分别列出单号与合计，而不报单一数字。")

    say("")
    say("## 结论")
    say()
    ok = (
        result.status == "conflict"
        and len(result.conflict_sheets) >= 2
        and len(set(result.conflict_totals)) >= 2
    )
    say(f"- 自动判定：{'通过' if ok else '未达预期'}（分歧场景状态 = `{result.status}`）")
    say(f"- 分歧两组：{'；'.join(result.conflict_sheets)}")
    say(f"- 原始响应保存在 `{OUT}/`。")

    (OUT / "transcript.md").write_text("\n".join(TRANSCRIPT) + "\n", encoding="utf-8")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

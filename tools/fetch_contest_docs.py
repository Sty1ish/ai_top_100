"""로그인된 크롬(디버깅 포트 9333)에서 aitop100.org 대회 문서를 읽어 docs/contest/에 텍스트로 저장한다.

python tools/fetch_contest_docs.py [시작 URL ...]
같은 사이트의 /contest/ 아래 링크를 따라가며 모두 저장한다.
"""

import pathlib
import re
import sys
import time

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "contest"
START = sys.argv[1:] or ["https://aitop100.org/contest/docs", "https://aitop100.org/contest/docs/test-cases"]

OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9333")
    page = browser.contexts[0].new_page()
    queue, seen = list(START), set()

    while queue:
        url = queue.pop(0).split("#")[0]
        if url in seen:
            continue
        seen.add(url)

        page.goto(url)
        time.sleep(1.5)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        text = page.evaluate("document.querySelector('main')?.innerText || document.body.innerText")
        name = re.sub(r"[^\w-]+", "_", url.split("/contest/", 1)[-1]).strip("_") or "index"
        (OUT / f"{name}.md").write_text(f"<!-- {url} -->\n{text}\n", encoding="utf-8")
        print(f"{len(text):6d} {url}")

        for href in page.evaluate("[...document.querySelectorAll('a')].map(a => a.href)"):
            href = href.split("#")[0]
            if href.startswith("https://aitop100.org/contest/docs") and href not in seen:
                queue.append(href)

    page.close()

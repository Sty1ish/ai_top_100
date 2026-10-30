"""브라우저를 띄워 사용자가 직접 카카오 로그인하면, 로그인한 상태로 봇 문서 전체를 텍스트로 저장한다.

사용법: python tools/fetch_kakao_bot_docs.py <프로필 폴더> <저장 폴더>
"""

import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

START = "https://developers.kakao.com/docs/ko/bot/common"
LOGIN = "https://accounts.kakao.com/login/?continue=" + START
PREFIX = "https://developers.kakao.com/docs/ko/bot/"

profile_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)


def doc_loaded(page) -> bool:
    if not page.url.startswith(PREFIX):
        return False

    text = page.locator("body").inner_text(timeout=5000)

    return len(text) > 1500 and "404" not in text[:300]


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(str(profile_dir), headless=False, viewport={"width": 1280, "height": 900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(LOGIN)

    print("브라우저 창에서 카카오 로그인을 진행해 주세요 (최대 15분 대기)", flush=True)

    deadline = time.time() + 900

    while time.time() < deadline:
        time.sleep(5)

        try:
            page = ctx.pages[-1]
            body = page.locator("body").inner_text(timeout=5000)
            print("현재:", page.url, len(body), body[:120].replace("\n", " | "), flush=True)

            if page.url.startswith("https://developers.kakao.com") and not page.url.startswith(PREFIX):
                page.goto(START)
                page.wait_for_load_state("networkidle")

            if doc_loaded(page):
                break
        except Exception as e:
            print("대기 중:", type(e).__name__, e, flush=True)
    else:
        print("LOGIN_TIMEOUT", flush=True)
        sys.exit(1)

    print("LOGGED_IN, 문서 수집 시작", flush=True)

    queue, seen = [START], set()

    while queue:
        url = queue.pop(0).split("#")[0]

        if url in seen:
            continue

        seen.add(url)
        page.goto(url)
        page.wait_for_load_state("networkidle")

        for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
            href = href.split("#")[0].split("?")[0]

            if href.startswith(PREFIX) and href not in seen:
                queue.append(href)

        main = page.locator("main")
        text = main.inner_text() if main.count() else page.locator("body").inner_text()

        name = re.sub(r"[^a-zA-Z0-9_-]+", "_", url.removeprefix(PREFIX)) or "index"
        (out_dir / f"{name}.md").write_text(f"# {page.title()}\n\nURL: {url}\n\n{text}\n", encoding="utf-8")
        print("저장:", url, len(text), flush=True)

    print("DONE", len(seen), "pages", flush=True)
    ctx.close()

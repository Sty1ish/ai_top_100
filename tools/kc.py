"""디버깅 포트(9333)로 띄운 크롬에 붙어 카카오 콘솔을 조작한다.

python tools/kc.py show [url]          현재(또는 이동한) 페이지 텍스트 출력 + 스크린샷
python tools/kc.py click "<텍스트>"     보이는 요소 중 텍스트가 일치하는 것을 클릭
python tools/kc.py fill "<css>" "<값>"  입력란 채우기
python tools/kc.py eval "<js>"          페이지에서 JS 실행 후 결과 출력
"""

import sys
import time

from playwright.sync_api import sync_playwright

SHOT = sys.argv[-1] if sys.argv[-1].endswith(".png") else None
args = [a for a in sys.argv[1:] if a != SHOT]

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9333")
    page = browser.contexts[0].pages[-1]
    cmd = args[0]

    if cmd == "show" and len(args) > 1:
        page.goto(args[1])
    elif cmd == "click":
        page.get_by_text(args[1], exact=True).locator("visible=true").first.click()
    elif cmd == "fill":
        page.locator(args[1]).first.fill(args[2])
    elif cmd == "eval":
        print(page.evaluate(args[1]))

    time.sleep(2)

    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass  # 상시 연결을 유지하는 페이지는 networkidle이 오지 않는다

    print("URL:", page.url)

    if cmd != "eval":
        main = page.locator("main")
        text = main.inner_text() if main.count() else page.locator("body").inner_text()
        print(text[:6000])

    if SHOT:
        page.screenshot(path=SHOT, full_page=True)

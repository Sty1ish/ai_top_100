"""로그인된 크롬(9333)에서 카카오톡 채널을 친구 추가한다. python tools/add_channel.py <채널 URL>"""
import sys, time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    page = p.chromium.connect_over_cdp("http://127.0.0.1:9333").contexts[0].pages[-1]
    page.goto(sys.argv[1])
    time.sleep(2)
    for _ in range(2):
        # 첫 클릭은 채널 화면의 버튼, 두 번째는 확인 레이어의 버튼
        n = page.evaluate("""(() => {const b=[...document.querySelectorAll('button,a')].filter(e=>e.innerText.trim()==='채널 추가'&&e.offsetParent); if(b.length){b[b.length-1].click()} return b.length})()""")
        time.sleep(1.5)
    print("친구", page.evaluate("document.body.innerText.match(/친구 [\d,]+/)?.[0]"))

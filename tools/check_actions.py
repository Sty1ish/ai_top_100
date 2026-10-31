"""공개 TC L-11 모범 처리 순서(API-11 → 03 → 04 → 06 → 29 → 35)를 재현하고 초기화한다.

    set -a; . ~/ai_top_100/.env; set +a; ~/ai_top_100/.venv/bin/python tools/check_actions.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))

import actions  # noqa: E402

TC = "L-11"
ORDER = "100100016"  # API-11을 주문번호 순으로 훑어 로트가 맞는 주문을 찾음
LOT = "KD-WTM-370624-03"


async def main():
    ok = True

    async def step(name, args, expect=()):
        nonlocal ok
        r = await actions.call(name, args, TC)
        good = 200 <= r["status"] < 300 or r["status"] in expect
        ok &= good
        print(name, r["status"], json.dumps(actions.slim(name, r.get("data")), ensure_ascii=False)[:300])
        return r

    r = await step("API_11", {"order_no": ORDER})
    lots = {i["lot_id"] for s in r.get("data") or [] for i in s.get("items", [])}
    if LOT not in lots:
        print("경고: 배송 로트", lots, "에", LOT, "없음")
        ok = False
    await step("API_03", {"lot_id": LOT})
    await step("API_04", {"lot_id": LOT}, expect=(404,))  # 조사 기록 없음이 정답(본문 없는 404)
    await step("API_06", {"lot_id": LOT})
    await step("API_29", {"order_no": ORDER, "amount": 34900, "reason": "곰팡이 하자 전액환불"})
    await step("API_35", {
        "lane": "LANE2_HANDOFF",
        "reason": f"주문 {ORDER}, 로트 {LOT} 곰팡이 복수 신고, 조사 기록 없음. 안전 확인 및 판매자·공급 확인 요청",
    })

    r = await actions.call("resetMyActionData", {}, None)
    print("reset", r["status"])
    ok &= 200 <= r["status"] < 300
    print("OK" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)

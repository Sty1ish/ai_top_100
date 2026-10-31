"""docs/contest/openapi.json(JS 객체)에서 bot/action_specs.py를 만든다.

서버에는 node가 없으므로 로컬에서 실행하고 결과 파일을 커밋한다.
    python tools/gen_action_specs.py
"""

import json
import pprint
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "contest" / "openapi.json"
OUT = ROOT / "bot" / "action_specs.py"

# 요약만으로 모자란 경우의 한 줄 설명
DESC = {
    "API-02": "품목별 상품 검색 (item=SKU 품목 접두사)",
    "API-06": "로트별 상담(신고) 이력 검색",
    "API-11": "주문번호로 배송 검색 (배송 상태, 송장, 품목별 lot_id)",
    "API-22": "취소·실패 주문 복구 접수",
    "API-23": "주문 품목·수량 변경 접수",
    "API-28": "배송 건 재배송 접수",
    "API-29": "환불 신청 접수 (금액은 원 단위 정수)",
    "API-31": "카드 결제 취소 접수",
    "API-35": "담당자 이관 접수. lane은 자유 문자열(예: LANE2_HANDOFF 담당자 확인, LANE3_CLAIM 클레임). reason에 주문번호·사유·요청사항",
    "API-36": "에스컬레이션 접수 내역 조회",
    "API-37": "재배송비 고객 청구 접수",
}

JS = r"""
const fs = require('fs');
const window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
process.stdout.write(JSON.stringify(window.aitop100ActionsDocument));
"""


def main():
    raw = subprocess.run(["node", "-e", JS, str(SRC)], capture_output=True, check=True).stdout
    doc = json.loads(raw.decode("utf-8"))
    specs = {}
    for key, item in doc["paths"].items():
        path = key.split("?")[0]
        for method, op in item.items():
            oid = op["operationId"]
            params = [p for p in op.get("parameters", []) if p["in"] != "header"]
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema") or {}
            specs[oid] = {
                "method": method.upper(),
                "path": path,
                "path_params": [p["name"] for p in params if p["in"] == "path"],
                "query_params": [p["name"] for p in params if p["in"] == "query"],
                "body": {"properties": body.get("properties", {}), "required": body.get("required", [])},
                "has_body": "requestBody" in op,
                "desc": DESC.get(oid, op.get("summary", oid)),
            }
    text = '"""tools/gen_action_specs.py가 openapi.json에서 생성한 파일. 직접 고치지 않는다."""\n\nSPECS = ' + pprint.pformat(specs, width=120, sort_dicts=False) + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(f"{len(specs)}개 -> {OUT}")


if __name__ == "__main__":
    main()

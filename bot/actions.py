"""교동마을 산지직송 액션 API 클라이언트와 Claude 도구 스키마.

명세는 action_specs.py(생성 파일)에 있다. 도구 이름은 API_11처럼 쓴다.
"""

import asyncio
import logging
import os
from urllib.parse import quote

import httpx

from action_specs import SPECS

log = logging.getLogger("bot")

BASE = os.environ.get("ACTION_BASE", "https://gyodong-mall-action.aitop100.org").rstrip("/")
TIMEOUT = 10.0


def _tool_name(oid: str) -> str:
    return oid.replace("-", "_")


def _build_tool(oid: str, spec: dict) -> dict:
    props, required = {}, []
    for p in spec["path_params"] + spec["query_params"]:
        props[p] = {"type": "string"}
        required.append(p)
    for k, v in spec["body"]["properties"].items():
        s = {"type": v["type"]}
        if "enum" in v:
            s["enum"] = v["enum"]
        if v.get("format") == "date":
            s["description"] = "YYYY-MM-DD"
        if v["type"] == "array":
            s["items"] = {"type": v["items"]["type"]}
        props[k] = s
    required += spec["body"]["required"]
    prefix = "[접수] " if spec["method"] == "POST" else ""
    return {
        "name": _tool_name(oid),
        "description": prefix + spec["desc"],
        "input_schema": {"type": "object", "properties": props, "required": required},
    }


TOOL_SPECS: dict[str, dict] = {
    _tool_name(oid): _build_tool(oid, s) for oid, s in SPECS.items() if oid.startswith("API-")
}
_BY_TOOL = {_tool_name(oid): oid for oid in SPECS}

# 환불 접수는 근거를 밝혀야 한다 (압박·위협만으로 환불하지 않게)
REFUND_BASIS = ["하자확인", "단순변심", "규정반환"]
TOOL_SPECS["API_22"]["description"] += " 주문 취소가 아니다. 결제는 됐는데 주문이 유실·생성 실패(FAILED)한 경우의 복구만."
_r = TOOL_SPECS["API_29"]
_r["description"] += " basis: 하자확인=사진·기록·로트로 하자 확인, 단순변심=청약철회 조건 안에서 고객이 명시적으로 요청, 규정반환=출고 지연 취소·부족분 등 규정상 반환. 여론·신고·병원비 압박만으로는 접수하지 않는다."
_r["input_schema"]["properties"]["basis"] = {"type": "string", "enum": REFUND_BASIS}
_r["input_schema"]["required"].append("basis")


def _index_line(oid: str, spec: dict) -> str:
    keys = spec["path_params"] + spec["query_params"] + list(spec["body"]["properties"])
    mark = "[접수] " if spec["method"] == "POST" else ""
    return f"{_tool_name(oid)} {mark}{spec['desc'].split('.')[0][:30]}({','.join(keys)})"


# 분류에 묶이지 않은 범용 도구: 지금 도구 목록에 없는 API가 필요할 때 이름으로 부른다
API_CALL_TOOL = {
    "name": "api_call",
    "description": "액션 API를 이름으로 호출한다. 지금 도구 목록에 필요한 API가 없을 때 쓴다. 조회는 바로 실행되고, [접수]는 대상 주문을 먼저 조회했고 고객이 명시적으로 요청했을 때만 쓴다. 목록: "
    + "; ".join(_index_line(oid, sp) for oid, sp in SPECS.items() if oid.startswith("API-")),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": [_tool_name(o) for o in SPECS if o.startswith("API-")]},
            "args": {"type": "object", "description": "그 API의 인자 (예: {\"order_no\": \"100100016\"})"},
        },
        "required": ["name", "args"],
    },
}


def _g(*nums: int) -> list[str]:
    return [f"API_{n:02d}" for n in nums]


# 분류별 도구 (PLAN 2-1). 메모(26)·이관(35)·이관 조회(36)는 기타 외 모든 분류에 넣는다
_COMMON = _g(26, 35, 36)
GROUPS: dict[str, list[str]] = {
    "delivery": _g(7, 10, 11, 23, 24, 25, 27, 28, 37, 38) + _COMMON,
    "quality": _g(1, 3, 4, 5, 6, 7, 10, 11, 28, 29, 30, 37) + _COMMON,
    "refund": _g(7, 11, 17, 18, 22, 23, 29, 30, 31) + _COMMON,
    "payment": _g(7, 8, 17, 18, 22, 29, 30, 31, 32) + _COMMON,  # 결제·주문 대사(복구 22, 환불 29) 포함
    "promo": _g(7, 8, 9, 12, 13, 14, 15, 16, 20, 33, 34) + _COMMON,
    "account": _g(5, 7, 8, 12, 13, 14, 15, 19, 40) + _COMMON,
    "product": _g(1, 2, 3, 19, 21, 39, 40) + _COMMON,
    "other": _g(35),
}


def tools_for(categories) -> list[dict]:
    """분류 목록에 해당하는 도구 스키마를 번호 순으로 돌려준다 (캐싱을 위해 순서 고정)."""
    names = sorted({n for c in categories for n in GROUPS.get(c, [])})
    return [TOOL_SPECS[n] for n in names]


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT)
    return _client


async def call(name: str, args: dict, tc_id: str | None) -> dict:
    """도구 이름(API_11)과 인자로 액션 API를 호출한다. {"status", "data"}를 돌려준다."""
    oid = _BY_TOOL.get(name) or _BY_TOOL.get(_tool_name(name))
    if not oid:
        return {"status": 0, "error": f"알 수 없는 도구 {name}"}
    spec = SPECS[oid]
    if tc_id is None and oid.startswith("API-"):
        return {"status": 0, "error": "TC ID 없음"}
    args = dict(args or {})

    path = spec["path"]
    for p in spec["path_params"]:
        if not args.get(p):
            return {"status": 0, "error": f"{p} 필요"}
        path = path.replace("{" + p + "}", quote(str(args.pop(p)), safe=""))
    params = {p: str(args.pop(p)) for p in spec["query_params"] if p in args}
    body = {k: v for k, v in args.items() if k in spec["body"]["properties"]} if spec["has_body"] else None

    headers = {"Authorization": f"Bearer {os.environ.get('ACTION_TOKEN', '')}"}
    if tc_id is not None:
        headers["X-Aitop100-Test-Case-Id"] = tc_id

    retried = False
    while True:
        try:
            r = await _http().request(spec["method"], path, params=params or None, json=body, headers=headers)
        except httpx.HTTPError as e:
            if not retried:
                retried = True
                continue
            log.warning("action %s 실패: %r", oid, e)
            return {"status": 0, "error": "연결 실패"}
        if r.status_code in (429, 500, 503) and not retried:
            retried = True
            if r.status_code == 429:
                try:
                    wait = float(r.headers.get("Retry-After", "1"))
                except ValueError:
                    wait = 1.0
                await asyncio.sleep(min(wait, 20.0))
            else:
                await asyncio.sleep(0.5)
            continue
        break

    data = None
    if r.content:
        try:
            data = r.json()
        except ValueError:
            data = r.text[:200]
    log.info("action %s %s -> %s", oid, path, r.status_code)
    out = {"status": r.status_code, "data": data}
    if r.status_code == 404 and data is None:
        out["note"] = "없음"
    elif r.status_code == 400 and data is None:
        out["note"] = "파라미터 오류"
    return out


def slim(name: str, data, _depth: int = 0):
    """모델에 돌려줄 응답을 줄인다: null·빈 값 제거, 긴 문자열 자르기, 배열 10개까지."""
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = slim(name, v, _depth + 1)
        return out
    if isinstance(data, list):
        items = [slim(name, v, _depth + 1) for v in data[:10]]
        if len(data) > 10:
            items.append(f"...외 {len(data) - 10}건")
        return items
    if isinstance(data, str) and len(data) > 200:
        return data[:200] + "…"
    return data

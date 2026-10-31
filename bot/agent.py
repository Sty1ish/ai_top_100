"""CS 에이전트 코어: 분류(Julia-1) → 프롬프트 조립 → Claude 도구 호출 루프 → 답장 문자열.

마감(기본 45초)과 스텝 수(6)를 넘기지 않도록, 마지막 스텝이나 시간이 모자랄 때는 도구 없이 답하게 한다.
"""

import asyncio
import json
import logging
import os
import re
import time

import anthropic

import actions
import classify
import prompt
import session as sessions

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
MAX_STEPS = int(os.getenv("MAX_STEPS", "6"))
# 300이면 이관 사유(reason)를 쓰다 잘린다. 답 길이는 프롬프트로 줄인다
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "600"))
DEADLINE = float(os.getenv("ANSWER_DEADLINE", "45"))
# 남은 시간이 이보다 적으면 도구 없이 바로 답한다
FINAL_RESERVE = 12
# 답이 이보다 길면 문장 끝에서 자른다 (심사 한도 500자, 목표 150자)
TRIM_AT = int(os.getenv("TRIM_AT", "300"))
# 답이 이보다 길고 시간이 남으면 한 번 줄여 쓴다
REWRITE_AT = int(os.getenv("REWRITE_AT", "220"))
REWRITE_MODEL = os.getenv("REWRITE_MODEL", "claude-haiku-5-5")

FALLBACK = "확인에 시간이 걸리고 있어요. 잠시 후 다시 한 번 말씀해 주세요."
EMPTY = "다시 한 번 말씀해 주세요."

# 고객에게 내부 도구 이름이 새지 않게 마지막에 지운다 (예: "(API_07)", "API-35로")
_COMPLAINT = re.compile(r"곰팡|상했|상한|썩|부패|배탈|하자|파손|깨졌|안 ?와|안 ?왔|늦|화나|짜증|실망|불만|만 왔|일부만|오배송|잘못 왔|셔요|너무 셔|이상해|광고|따지")
_APOLOGY = re.compile(r"죄송|사과|송구")
_ORDER_IN = re.compile(r"\b1\d{8}\b")
_API_NAME = re.compile(r"\s*\(?\bAPI[_-]\d+\)?")
_HANGUL = re.compile(r"[가-힣]")
# 고객이 말하지 않은 회원ID·승인번호·전화번호 (답에서 가림)
_PRIVATE = re.compile(r"(?<![A-Za-z0-9-])(?:MB-\d{3,}|PAY-[\d-]{4,}\d|01[016-9]-?\d{3,4}-?\d{4})(?![\d-])")
# 접수 기록이 없는데 "전달했다/접수했다"고 말하는 문장 (거짓 완료 안내 방지)
_CLAIM = re.compile(r"[^.!?]*(전달했|전달드렸|전달해 ?(두었|드렸)|접수했|접수되었|접수됐|접수해 ?드렸|접수를 ?마쳤)[^.!?]*[.!?]?\s*")

log = logging.getLogger("bot")
client = anthropic.AsyncAnthropic(max_retries=1)


async def answer(user_key: str, text: str, images: list[str] | None = None, received_at: float | None = None) -> dict:
    """한 메시지를 처리해 {"text", "trace"}를 돌려준다. 같은 사용자의 메시지는 순서대로 처리한다."""
    received_at = received_at or time.monotonic()
    s = sessions.get(user_key)

    async with s.lock:
        return await _answer(s, text, images or [], received_at + DEADLINE)


async def _answer(s: sessions.Session, text: str, images: list[str], deadline: float) -> dict:
    trace = {"calls": [], "usage": [], "categories": None, "classify": None}
    t0 = time.monotonic()

    s.observe(text)
    s.said += "\n" + text

    # 분류 진입점은 classify.choose 하나 (규칙 우선, 규칙에 없을 때만 확신 높은 모델 1순위)
    chosen = await classify.choose(text, s.last_user_text)
    s.add_categories([c for c, _ in chosen])
    s.last_user_text = text
    trace["classify"] = [(c, round(p, 2)) for c, p in chosen]
    trace["classify_sec"] = round(time.monotonic() - t0, 2)

    user_msg = {"role": "user", "content": _user_content(text, images)}
    turn = [user_msg]
    reply = None

    for step in range(MAX_STEPS):
        remaining = deadline - time.monotonic()
        final = step == MAX_STEPS - 1 or remaining < FINAL_RESERVE

        try:
            res = await _create(s, s.history() + turn, final, remaining)
        except anthropic.BadRequestError as e:
            # 이미지 URL을 못 읽는 경우 등: 이미지를 빼고 한 번 더
            if images:
                log.warning("bad request with images, retrying without: %s", e)
                images = []
                turn[0] = user_msg = {"role": "user", "content": _user_content(text + "\n[첨부 사진을 열 수 없음]", [])}
                continue

            log.exception("claude bad request")
            break
        except Exception:
            log.exception("claude call failed")
            break

        u = res.usage
        trace["usage"].append(
            {
                "in": u.input_tokens,
                "cache_read": u.cache_read_input_tokens or 0,
                "cache_write": u.cache_creation_input_tokens or 0,
                "out": u.output_tokens,
                "stop": res.stop_reason,
                "blocks": [b.type for b in res.content],
            }
        )

        content = [_block(b) for b in res.content if b.type in ("text", "tool_use")]

        # 잘린 도구 호출은 버린다 (짝 없는 tool_use는 다음 요청을 깨뜨림)
        if res.stop_reason == "max_tokens":
            content = [b for b in content if b["type"] == "text"] or [{"type": "text", "text": EMPTY}]
        turn.append({"role": "assistant", "content": content})
        uses = [b for b in content if b["type"] == "tool_use"]

        if res.stop_reason != "tool_use" or not uses:
            reply = "".join(b["text"] for b in content if b["type"] == "text").strip()
            break

        results = await asyncio.gather(*(_run_tool(s, b, trace) for b in uses))
        turn.append({"role": "user", "content": list(results)})

    trace["categories"] = list(s.categories)
    trace["sec"] = round(time.monotonic() - t0, 2)

    if reply is None:
        # 실패한 턴은 이력에 남기지 않되, 고객 발화는 남겨 맥락을 잇는다
        s.add_turn([user_msg, {"role": "assistant", "content": [{"type": "text", "text": FALLBACK}]}])

        return {"text": FALLBACK, "trace": trace}

    reply = " ".join(_API_NAME.sub("", reply).split())

    # 한글이 없는 긴 문장(모델의 영어 내부 판단)은 고객에게 보내지 않는다
    kept = [x for x in re.split(r"(?<=[.!?])\s+", reply) if _HANGUL.search(x) or len(x) < 15]
    reply = " ".join(kept) or reply

    # 고객이 이 세션에서 말하지 않은 식별번호(회원ID·승인번호·전화번호)는 답에 내보내지 않는다
    reply = _PRIVATE.sub(lambda m: m.group(0) if m.group(0) in s.said else "(확인된 정보)", reply)

    # 접수(API) 기록이 없으면 "전달·접수했다"는 문장을 뺀다
    if not s.notes.get("접수 완료") and _CLAIM.search(reply):
        reply = _CLAIM.sub("", reply).strip() or reply

    if len(reply) > REWRITE_AT and deadline - time.monotonic() > 10:
        t1 = time.monotonic()
        reply = await _shorten(reply, deadline - time.monotonic() - 2) or reply
        trace["rewrite_sec"] = round(time.monotonic() - t1, 2)

    reply = _trim(reply) or EMPTY

    # 불만·하자 신고 턴인데 사과가 없으면 짧게 붙인다 (세션당 한 번)
    if not s.apologized and _COMPLAINT.search(text) and not _APOLOGY.search(reply):
        reply = _trim("불편을 드려 죄송합니다. " + reply)

    s.apologized = s.apologized or bool(_APOLOGY.search(reply))

    # 이력에는 실제로 보낸 답을 남긴다 (줄여 쓰기·문장 제거 반영)
    if turn[-1]["role"] == "assistant":
        turn[-1] = {"role": "assistant", "content": [{"type": "text", "text": reply}]}

    s.add_turn(_strip_images(turn))
    log.info("agent tc=%s cats=%s calls=%s usage=%s sec=%s", s.tc_id, trace["categories"], trace["calls"], trace["usage"], trace["sec"])

    return {"text": reply, "trace": trace}


async def _create(s, messages: list[dict], final: bool, remaining: float):
    messages = _mark_last(messages)
    # 생각(thinking)은 지연·토큰만 늘려서 끈다
    kwargs = {"thinking": {"type": "between_tools" if "sonnet" in MODEL or "opus" in MODEL else "disabled"}}

    if final:
        kwargs["tool_choice"] = {"type": "none"}

    return await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=prompt.build_system(s),
        tools=prompt.build_tools(s.categories),
        messages=messages,
        timeout=max(5.0, min(remaining - 2, 30.0)),
        **kwargs,
    )


async def _shorten(text: str, budget: float) -> str | None:
    """긴 답을 150자 안쪽으로 줄인다. 실패하면 None (원문을 그대로 씀)."""
    try:
        res = await client.messages.create(
            model=REWRITE_MODEL,
            max_tokens=300,
            system="고객 상담 답장을 150자 이내 한국어 존댓말 한 문단으로 줄여 쓴다. 사실·숫자·날짜·금액·접수번호·고객에게 묻는 질문은 유지하고, 인사·반복·부연은 뺀다. 새 내용을 더하지 않는다. 줄인 답장만 출력한다.",
            messages=[{"role": "user", "content": text}],
            timeout=max(2.0, min(budget, 8.0)),
        )
        out = "".join(b.text for b in res.content if b.type == "text").strip()

        return out if 0 < len(out) < len(text) else None
    except Exception as e:
        log.warning("shorten failed: %s", e)

        return None


def _mark_last(messages: list[dict]) -> list[dict]:
    """마지막 메시지 블록에 캐시 지점을 둬서 같은 턴의 다음 스텝이 앞부분을 재사용하게 한다."""
    out = messages[:-1]
    last = messages[-1]
    content = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
    content = content[:-1] + [{**content[-1], "cache_control": prompt.CACHE}]

    return out + [{**last, "content": content}]


async def _run_tool(s: sessions.Session, block: dict, trace: dict) -> dict:
    name, args = block["name"], block["input"] or {}
    t0 = time.monotonic()

    try:
        if name == "load_policy":
            cat = args.get("category", "")
            s.add_categories([cat] if cat in classify.CATEGORIES else [])
            out = prompt.read_policy(cat) or "해당 분류의 지식이 없습니다."
        elif name == "refund_terms":
            out = prompt.refund_terms(str(args.get("received_date", "")), s.today)
        elif name == "load_doc":
            out = prompt.read_doc(str(args.get("name", "")))
        elif name == "ledger_lookup":
            out = prompt.ledger_lookup(str(args.get("kind", "")), str(args.get("query", "")))
        elif name == "api_call":
            # 범용 호출도 개별 도구와 같은 확인(조회 후 접수, 중복 차단)을 거친다
            name = str(args.get("name", "")).replace("-", "_")
            args = args.get("args") or {}
            args = json.loads(args) if isinstance(args, str) else args

            # 모델이 {"name", "args"}를 한 겹 더 감싸 보내는 경우를 푼다
            while isinstance(args, dict) and isinstance(args.get("args"), dict) and set(args) <= {"name", "args"}:
                args = args["args"]
            out = await _call_action(s, name, args)
        elif name == "date_calc":
            out = prompt.date_calc(**{k: v for k, v in args.items() if k in ("op", "date1", "date2", "days")})
        else:
            out = await _call_action(s, name, args)
    except Exception as e:
        log.exception("tool %s failed", name)
        out = {"error": f"도구 오류: {e}"}

    trace["calls"].append({"name": name, "args": args, "sec": round(time.monotonic() - t0, 2), "status": out.get("status") if isinstance(out, dict) else None})
    body = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)

    return {"type": "tool_result", "tool_use_id": block["id"], "content": body[:3000]}


async def _call_action(s: sessions.Session, name: str, args: dict) -> dict:
    spec = actions.TOOL_SPECS.get(name)

    if spec is None:
        return {"error": f"{name}은 지금 쓸 수 없는 도구입니다."}

    orders = _find(args, "order_no")
    submit = "[접수]" in spec.get("description", "")

    # 접수 전에 그 주문을 조회했는지 확인한다 (엉뚱한 주문에 접수하는 것을 막음)
    if submit and orders and not all(o in s.looked_up for o in orders):
        return {"status": 409, "data": "접수 전에 고객이 확인한 이 주문을 먼저 조회(API-07 또는 API-11)하세요."}

    # 같은 세션에서 같은 호출을 되풀이하지 않는다 (재현율·정밀도 감점, 접수 덮어쓰기 방지)
    key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"

    if key in s.called:
        # 같은 조회는 다시 부르지 않고 앞선 결과를 돌려준다. 접수는 되풀이하지 않는다
        if key in s.cache and not submit:
            return {**s.cache[key], "note": "같은 세션에서 앞서 조회한 결과입니다."}

        return {"status": 409, "data": "이미 같은 호출을 했습니다. 이전 결과를 쓰세요."}

    if name in ("API_29", "API-29"):
        basis = args.get("basis")

        if basis not in actions.REFUND_BASIS:
            return {"status": 409, "data": "환불 근거(basis)가 없습니다. 하자 확인·청약철회 조건의 명시적 요청·규정상 반환일 때만 접수하고, 압박·위협 사안은 공감 후 이관(LANE3_CLAIM 또는 LANE2_HANDOFF)하세요."}

    if name in ("API_35", "API-35"):
        # lane은 지정값만 (예: "LANE2_HANDOFF 담당자 확인" → "LANE2_HANDOFF")
        if m := re.match(r"\s*(LANE\d_[A-Z_]+?)(?:_?\s|$)", str(args.get("lane", ""))):
            args = {**args, "lane": m.group(1)}

        esc = f"{args.get('lane')}:{','.join(orders) or _ORDER_IN.findall(str(args.get('reason', '')))[:1]}"

        if esc in s.escalated:
            return {"status": 409, "data": "같은 주문·같은 lane으로 이미 이관했습니다. 앞선 접수번호를 안내하세요."}

    res = await actions.call(name, args, s.tc_id)
    s.called.add(key)
    status = res.get("status", 0)

    if 200 <= status < 300:
        for o in orders:
            s.looked_up.add(o)
            s.note("확인한 주문번호", o)

        for m in _find(args, "member_id"):
            s.note("회원ID", m)

        if name in ("API_35", "API-35"):
            s.escalated.add(esc)

        if submit:
            s.note("접수 완료", f"{name} {json.dumps(res.get('data'), ensure_ascii=False)[:120]}")

        out = {"status": status, "data": actions.slim(name, res.get("data"))}
        s.cache[key] = out

        return out

    return res


def _trim(text: str, limit: int = TRIM_AT) -> str:
    """안전장치: 너무 길면 limit 안쪽의 마지막 문장 끝에서 자른다."""
    text = " ".join(text.split())

    if len(text) <= limit:
        return text

    cut = max(text.rfind(p, 0, limit) for p in (". ", "? ", "! ", "다.", "요."))

    return text[: cut + 2].strip() if cut > limit // 3 else text[:limit].rstrip() + "…"


def _find(node, key: str) -> list[str]:
    if isinstance(node, dict):
        found = [str(v) for k, v in node.items() if k == key and v not in (None, "")]

        for v in node.values():
            if isinstance(v, (dict, list)):
                found += _find(v, key)

        return found

    if isinstance(node, list):
        return [x for item in node for x in _find(item, key)]

    return []


def _user_content(text: str, images: list[str]) -> list[dict]:
    blocks = [{"type": "image", "source": {"type": "url", "url": url}} for url in images[:3]]

    return blocks + [{"type": "text", "text": text or "(사진)"}]


def _block(b) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}

    return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}


def _strip_images(turn: list[dict]) -> list[dict]:
    """지난 턴의 이미지는 다시 보내지 않는다 (모델이 이미 내용을 되짚었음)."""
    out = []

    for m in turn:
        if isinstance(m["content"], list) and any(b.get("type") == "image" for b in m["content"]):
            content = [b if b.get("type") != "image" else {"type": "text", "text": "[고객이 보낸 사진]"} for b in m["content"]]
            m = {**m, "content": content}

        out.append(m)

    return out

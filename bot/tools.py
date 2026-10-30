"""에이전트가 호출하는 도구. 계산·집계·데이터 조회는 LLM이 아니라 여기서 정확히 한다.

새 도구는 함수를 만들고 @tool(설명, 파라미터 스키마)로 등록하면 된다.
도구가 'table'을 돌려주면 render가 그 값을 그대로 정산서(ItemCard)로 그린다.
"""

import ast
import json
import logging
import operator

log = logging.getLogger("bot")

TOOLS: dict[str, dict] = {}


def tool(description: str, parameters: dict):
    def register(fn):
        TOOLS[fn.__name__] = {
            "fn": fn,
            "spec": {
                "type": "function",
                "function": {"name": fn.__name__, "description": description, "parameters": parameters},
            },
        }

        return fn

    return register


def specs() -> list[dict]:
    return [t["spec"] for t in TOOLS.values()]


def run(name: str, arguments: dict | str) -> dict:
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")

    if name not in TOOLS:
        return {"error": f"없는 도구: {name}"}

    try:
        result = TOOLS[name]["fn"](**arguments)
    except Exception as e:
        log.warning("tool %s failed: %s", name, e)
        result = {"error": str(e)}

    log.info("tool %s(%s) -> %s", name, json.dumps(arguments, ensure_ascii=False)[:200], str(result)[:200])

    return result


def won(n: float) -> str:
    return f"{round(n):,}원"


# ----------------------------- 계산 -----------------------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value

    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))

    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))

    raise ValueError("숫자와 + - * / % ** 괄호만 쓸 수 있습니다")


@tool(
    "사칙연산 계산기. 금액·수량·비율 등 숫자 계산은 반드시 이 도구로 한다.",
    {
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "예: (30*4500 + 12*9000) * 0.9"}},
        "required": ["expression"],
    },
)
def calculate(expression: str) -> dict:
    value = _eval(ast.parse(expression.replace(",", ""), mode="eval"))

    return {"expression": expression, "result": round(value, 4)}


@tool(
    "납품·판매 정산. 품목별 수량×단가와 합계를 계산한다. 수수료율이 있으면 공제 후 지급액도 계산한다.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "정산서 제목, 예: 갯마을 김씨 정산"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": "string", "description": "kg, 박스, 개 등"},
                        "unit_price": {"type": "number", "description": "단위당 가격(원)"},
                    },
                    "required": ["name", "quantity", "unit_price"],
                },
            },
            "fee_rate": {"type": "number", "description": "수수료율(%), 없으면 0"},
        },
        "required": ["items"],
    },
)
def settle(items: list[dict], title: str = "정산 내역", fee_rate: float = 0) -> dict:
    rows, total = [], 0

    for it in items:
        amount = float(it["quantity"]) * float(it["unit_price"])
        total += amount
        qty = f"{it['quantity']:g}{it.get('unit') or ''}"
        rows.append({"label": it["name"], "value": f"{qty} × {won(it['unit_price'])} = {won(amount)}"})

    result = {"title": title, "total": round(total), "rows": rows}

    if fee_rate:
        fee = total * fee_rate / 100
        rows.append({"label": "수수료", "value": f"{fee_rate:g}% = -{won(fee)}"})
        result["fee"] = round(fee)
        result["payout"] = round(total - fee)

    result["table"] = {
        "title": title,
        "rows": rows,
        "total": {"label": "지급액" if fee_rate else "합계", "value": won(result.get("payout", total))},
    }

    return result

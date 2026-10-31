"""10명 채팅방 전체 텍스트를 knowledge/raw/full/<이름>.txt로 저장한다."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import kakao_pc as k

sys.stdout.reconfigure(encoding="utf-8")
OUT = pathlib.Path(__file__).resolve().parent.parent / "knowledge" / "raw" / "full"
for name in sys.argv[1:] or ["이민준", "구방만", "고길동", "홍보라", "박수복", "홍이장", "박민아", "조억만", "김청년", "홍보걸"]:
    try:
        h = k.open_room(name)
        text = k.read_all(h)
        (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
        print(name, len(text), flush=True)
    except BaseException as e:  # noqa: BLE001
        print(name, "오류", e, flush=True)

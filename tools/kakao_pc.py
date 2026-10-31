"""카카오톡 PC 앱으로 마을 관계자 채널과 1:1 대화한다 (사람 속도로 한 번에 한 질문).

python tools/kakao_pc.py open <채팅방 이름>          채팅 목록에서 검색해 방을 연다
python tools/kakao_pc.py read <채팅방 이름> [줄 수]   대화 내용 끝부분 출력
python tools/kakao_pc.py ask <채팅방 이름> <질문>     질문을 보내고 답이 올 때까지(최대 90초) 기다려 출력

대화 원문은 knowledge/raw/<이름>.md에 시각과 함께 누적 저장한다.
"""

import contextlib
import ctypes
import msvcrt
import re
import os
import datetime
import pathlib
import sys
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

RAW = pathlib.Path(__file__).resolve().parent.parent / "knowledge" / "raw"
LOCK = pathlib.Path(__file__).resolve().parent / ".kakao_pc.lock"
user32 = ctypes.windll.user32


@contextlib.contextmanager
def ui_lock():
    # 여러 대화를 동시에 진행해도 화면 조작(포커스·클립보드)은 한 번에 하나만
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT)
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            time.sleep(0.2)
    try:
        yield
    finally:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def _kakao_windows():
    out = []

    def cb(h, _):
        _, pid = win32process.GetWindowThreadProcessId(h)
        try:
            exe = win32process.GetModuleFileNameEx(
                win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid), 0
            )
        except Exception:
            return True
        if exe.lower().endswith("kakaotalk.exe") and win32gui.IsWindowVisible(h):
            out.append(h)
        return True

    win32gui.EnumWindows(cb, None)
    return out


def _children(h, cls):
    found = []
    win32gui.EnumChildWindows(h, lambda c, _: found.append(c) or True if win32gui.GetClassName(c) == cls else True, None)
    return found


def _post_key(h, vk, ctrl=False):
    # 포커스를 빼앗지 않고 키 입력을 보낸다 (Ctrl 조합은 키보드 상태를 잠시 바꿔서 전달)
    if ctrl:
        state = (ctypes.c_ubyte * 256)()
        user32.GetKeyboardState(state)
        old = state[win32con.VK_CONTROL]
        state[win32con.VK_CONTROL] = 0x80
        user32.SetKeyboardState(state)
    win32api.PostMessage(h, win32con.WM_KEYDOWN, vk, 0)
    time.sleep(0.05)
    win32api.PostMessage(h, win32con.WM_KEYUP, vk, 0xC0000001)
    if ctrl:
        time.sleep(0.05)
        state[win32con.VK_CONTROL] = old
        user32.SetKeyboardState(state)


def main_window():
    for h in _kakao_windows():
        if win32gui.GetWindowText(h) == "카카오톡":
            return h
    raise SystemExit("카카오톡 메인 창을 찾지 못했습니다")


def chat_window(name):
    for h in _kakao_windows():
        if win32gui.GetWindowText(h) == name:
            return h
    return None


def open_room(name):
    if (h := chat_window(name)) is not None:
        return h

    main = main_window()
    edits = [e for e in _children(main, "Edit") if win32gui.IsWindowVisible(e)]
    if not edits:
        raise SystemExit("채팅 목록 검색창이 보이지 않습니다. 메인 창에서 채팅 탭을 열어 주세요")

    search = edits[0]
    # 새로 추가한 채널은 목록 갱신이 늦어 첫 검색에 안 잡힐 수 있어 검색창을 비웠다가 다시 검색한다
    for _ in range(3):
        win32api.SendMessage(search, win32con.WM_SETTEXT, 0, "")
        time.sleep(1.0)
        win32api.SendMessage(search, win32con.WM_SETTEXT, 0, name)
        time.sleep(1.0)
        _post_key(search, win32con.VK_RETURN)
        for _ in range(10):
            time.sleep(0.3)
            if (h := chat_window(name)) is not None:
                win32api.SendMessage(search, win32con.WM_SETTEXT, 0, "")
                return h

    raise SystemExit(f"'{name}' 채팅방을 열지 못했습니다")


def read_all(h):
    with ui_lock():
        return _read_all(h)


def _read_all(h):
    lists = _children(h, "EVA_VH_ListControl_Dblclk")
    if not lists:
        return ""

    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.CloseClipboard()

    # 메시지 목록은 포커스가 있어야 Ctrl+A/C가 먹는다. 창을 앞으로 가져와 목록 왼쪽 여백을 클릭한다
    win32gui.ShowWindow(h, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32gui.SetForegroundWindow(h)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)
    l, t, r, b = win32gui.GetWindowRect(lists[0])
    win32api.SetCursorPos((l + 6, (t + b) // 2))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
    time.sleep(0.15)
    for key in (ord("A"), ord("C")):
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(key, 0, 0, 0)
        win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.25)

    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except TypeError:
        return ""
    finally:
        win32clipboard.CloseClipboard()


def _key(vk, ctrl=False):
    if ctrl:
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    win32api.keybd_event(vk, 0, 0, 0)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    if ctrl:
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.15)


def send(h, text):
    with ui_lock():
        _send(h, text)


def _send(h, text):
    # WM_SETTEXT로 넣으면 카카오톡이 입력으로 인식하지 않아 전송 버튼이 비활성이다. 클립보드 붙여넣기로 입력한다
    edit = _children(h, "RICHEDIT50W")[0]
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    win32clipboard.CloseClipboard()

    win32gui.ShowWindow(h, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32gui.SetForegroundWindow(h)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)
    l, t, r, b = win32gui.GetWindowRect(edit)
    win32api.SetCursorPos(((l + r) // 2, (t + b) // 2))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
    time.sleep(0.2)
    _key(ord("A"), ctrl=True)
    _key(win32con.VK_DELETE)
    _key(ord("V"), ctrl=True)
    time.sleep(0.3)
    _key(win32con.VK_RETURN)


TPL = pathlib.Path(__file__).resolve().parent / "tpl_fullview.png"


def _find_fullview(h):
    # 채팅창을 캡처해 "전체보기" 글자 위치를 템플릿 매칭으로 찾는다 (위에서 아래 순서의 화면 좌표)
    import numpy as np
    from PIL import Image, ImageGrab

    # 말풍선 배경이 흰색/회색으로 바뀌어도 맞도록 글자(어두운 픽셀)만 비교한다
    l, t, r, b = win32gui.GetWindowRect(h)
    shot = np.asarray(ImageGrab.grab(bbox=(l, t, r, b), all_screens=True).convert("L")) < 140
    tpl = np.asarray(Image.open(TPL).convert("L")) < 140
    th, tw = tpl.shape
    need = tpl.sum()
    hits = []
    for y in range(0, shot.shape[0] - th):
        for x in range(0, min(shot.shape[1] - tw, 200)):
            win = shot[y : y + th, x : x + tw]
            if win.sum() < need * 0.7:
                continue
            if (win == tpl).mean() > 0.93:
                if not hits or abs(hits[-1][1] - (t + y + th // 2)) > 10:
                    hits.append((l + x + tw // 2, t + y + th // 2))
    return hits


def _popup_text(h, pos):
    before = set(_kakao_windows())
    win32api.SetCursorPos(pos)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
    for _ in range(15):
        time.sleep(0.2)
        new = [w for w in set(_kakao_windows()) - before if w != h]
        if new:
            break
    else:
        return None

    w = new[0]
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.CloseClipboard()
    win32gui.SetForegroundWindow(w)
    time.sleep(0.2)
    l, t, r, b = win32gui.GetWindowRect(w)
    win32api.SetCursorPos(((l + r) // 2, (t + b) // 2))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
    time.sleep(0.15)
    _key(ord("A"), ctrl=True)
    _key(ord("C"), ctrl=True)
    time.sleep(0.2)
    win32clipboard.OpenClipboard()
    try:
        text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except TypeError:
        text = None
    finally:
        win32clipboard.CloseClipboard()
    _close_popups()
    return text


def _close_popups():
    # 열려 있는 전체보기 창("...님의 메시지")은 다른 채팅창을 가리므로 닫는다
    for w in _kakao_windows():
        if win32gui.GetWindowText(w).endswith("님의 메시지"):
            try:
                win32gui.PostMessage(w, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
    time.sleep(0.3)


def _wheel(h, notches):
    lists = _children(h, "EVA_VH_ListControl_Dblclk")
    l, t, r, b = win32gui.GetWindowRect(lists[0])
    win32api.SetCursorPos(((l + r) // 2, (t + b) // 2))
    for _ in range(abs(notches)):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, 120 if notches > 0 else -120, 0)
        time.sleep(0.03)
    time.sleep(0.3)


def expand(h, answer, max_pages=6):
    """답 중 "..."로 잘린 메시지를 전체보기 창의 전문으로 바꾼다.
    화면에 보이는 전체보기를 맨 아래부터 위로 스크롤하며 열고, 잘린 앞부분이 같은 전문으로 대체한다."""
    blocks = re.split(r"(?m)^(?=\[[^\]]+\] \[(?:오전|오후) )", answer)
    head_re = re.compile(r"^\[[^\]]+\] \[[^\]]+\] ")
    todo = {}
    for i, blk in enumerate(blocks):
        if blk.rstrip().endswith("..."):
            body = head_re.sub("", blk).strip()
            todo[i] = re.sub(r"\s+", "", body[:-3])[:40]
    if not todo:
        return answer

    with ui_lock():
        _close_popups()
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32gui.SetForegroundWindow(h)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.3)
        _wheel(h, -30)
        seen = set()
        for _ in range(max_pages):
            for pos in reversed(_find_fullview(h)):
                full = _popup_text(h, pos)
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32gui.SetForegroundWindow(h)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                if not full or full in seen:
                    continue
                seen.add(full)
                key = re.sub(r"\s+", "", full)
                for i, prefix in list(todo.items()):
                    if key.startswith(prefix):
                        head = head_re.match(blocks[i])
                        blocks[i] = (head.group(0) if head else "") + full.strip() + "\n"
                        del todo[i]
            if not todo:
                break
            _wheel(h, 3)
        _wheel(h, -30)
    return "".join(blocks)


def log(name, kind, text):
    RAW.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    with open(RAW / f"{name}.md", "a", encoding="utf-8") as f:
        f.write(f"\n### {stamp} {kind}\n{text}\n")


def ask(name, question, timeout=150):
    if len(question) > 200:
        raise SystemExit(f"200자 초과 ({len(question)}자)")

    h = open_room(name)
    before = read_all(h)
    send(h, question)
    log(name, "Q", question)

    # 내 질문이 반영된 직후 상태를 기준으로, 그 뒤에 늘어난 부분이 관계자의 답이다
    time.sleep(1.5)
    base = read_all(h)
    if len(base) <= len(before):
        base = before

    deadline = time.time() + timeout
    last, stable = base, 0
    time.sleep(15)
    while time.time() < deadline:
        now = read_all(h)
        if len(now) > len(base):
            # 답이 여러 말풍선으로 나뉘어 올 수 있어 한 번 더 읽어 늘지 않았으면 끝난 것으로 본다
            stable = stable + 1 if now == last else 0
            if stable >= 1:
                answer = expand(h, now[len(base):].strip())
                log(name, "A", answer)
                return answer
        last = now
        time.sleep(8)

    answer = read_all(h)[len(base):].strip()
    log(name, "A(timeout)", answer)
    return answer or "(시간 내 답 없음)"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd, name = sys.argv[1], sys.argv[2]

    if cmd == "open":
        print(hex(open_room(name)))
    elif cmd == "read":
        lines = read_all(open_room(name)).splitlines()
        print("\n".join(lines[-int(sys.argv[3]) if len(sys.argv) > 3 else -40:]))
    elif cmd == "ask":
        print(ask(name, sys.argv[3]))

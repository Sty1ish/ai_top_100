"""카카오톡 채팅방 창을 캡처한다. python tools/kshot.py <방 이름> <출력 png>"""
import sys, time, win32gui, win32api, win32con
from PIL import ImageGrab
sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import kakao_pc as k
with k.ui_lock():
    h = k.chat_window(sys.argv[1]) or k.open_room(sys.argv[1])
    win32gui.ShowWindow(h, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0); win32gui.SetForegroundWindow(h); win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.4)
    r = win32gui.GetWindowRect(h)
    ImageGrab.grab(bbox=r, all_screens=True).save(sys.argv[2])
    print(r)

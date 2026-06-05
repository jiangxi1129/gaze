"""屏幕截图 + 窗口选择 + perceptual hash 去重 (Windows only)

两种截屏模式：
1. screenshot_screen(bbox): ImageGrab.grab() — 从屏幕 buffer 截，看到啥截啥（被遮挡部分截不到，被其他窗口浮盖的内容会被截到）
2. screenshot_window(hwnd):  PrintWindow API — 从窗口应用 buffer 截，完全不受遮挡影响（gaze 默认用这个，避免 console 浮窗污染）
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image, ImageGrab

# Windows API
user32 = ctypes.windll.user32

# 尝试 import pywin32（PrintWindow 用），失败 fallback
try:
    import win32gui
    import win32ui
    _HAS_PYWIN32 = True
except ImportError:
    _HAS_PYWIN32 = False


def crop_borders(img: Image.Image, top: int = 0, bottom: int = 0, left: int = 0, right: int = 0) -> Image.Image:
    """裁掉图片四周固定像素。用于排除浏览器 toolbar/书签栏/任务栏等。"""
    w, h = img.size
    new_top = max(0, top)
    new_bottom = max(0, h - bottom)
    new_left = max(0, left)
    new_right = max(0, w - right)
    if new_top >= new_bottom or new_left >= new_right:
        return img  # 裁完没了，退回原图
    return img.crop((new_left, new_top, new_right, new_bottom))


def screenshot(
    window_title: str | None = None,
    use_printwindow: bool = False,
    fallback_to_fullscreen: bool = True,
) -> Image.Image:
    """截屏。默认用 ImageGrab + 窗口 bbox（截到完整窗口含 overlay/对话框）。

    Args:
        window_title: 窗口标题（模糊匹配，contains，不区分大小写）。None = 全屏
        use_printwindow: True 用 PrintWindow API；False 用 ImageGrab（默认）
        fallback_to_fullscreen: 找不到窗口时是否 fallback 全屏（默认 True）。
            浏览器 tab 标题会随切换变化，fuzzy match 容易失效，fallback 防止 gaze 卡住。

    Returns:
        PIL.Image (RGB)
    """
    if not window_title:
        return ImageGrab.grab(all_screens=False)

    hwnd = _find_window(window_title)
    if not hwnd:
        if fallback_to_fullscreen:
            # 安全降级：找不到指定窗口时截全屏，避免 gaze 整个卡死
            # （浏览器 tab 切换 / 窗口被关 / 标题动态变化等场景）
            return ImageGrab.grab(all_screens=False)
        raise ValueError(f"找不到窗口（标题含『{window_title}』）。试试 list_windows() 看可用窗口")

    # PrintWindow 模式（默认禁用，对 OpenGL/DirectX overlay 拿不到内容）
    if use_printwindow and _HAS_PYWIN32:
        return _printwindow_capture(hwnd)

    # 默认 ImageGrab + GetWindowRect 整窗口 bbox（含 overlay / 对话框 / 边框）
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise ValueError(f"窗口『{window_title}』最小化或不可见")
    bbox = (rect.left, rect.top, rect.right, rect.bottom)
    return ImageGrab.grab(bbox=bbox, all_screens=True)


def _printwindow_capture(hwnd: int) -> Image.Image:
    """用 Windows PrintWindow API 截窗口客户区（不受其他窗口遮挡）"""
    # GetClientRect 拿客户区大小（不含标题栏 / 边框）
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        raise ValueError(f"窗口客户区无效 ({width}x{height})")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(save_bitmap)

    # PW_RENDERFULLCONTENT = 0x00000002 — Windows 8.1+ 支持 DWM-rendered 窗口（如 Chrome / 游戏）
    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x00000002)

    bmpinfo = save_bitmap.GetInfo()
    bmpstr = save_bitmap.GetBitmapBits(True)

    img = Image.frombuffer(
        'RGB',
        (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
        bmpstr, 'raw', 'BGRX', 0, 1,
    )

    # cleanup
    win32gui.DeleteObject(save_bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    if result != 1:
        # PrintWindow 失败（某些游戏 anti-cheat 阻止）
        raise RuntimeError(f"PrintWindow failed (result={result})")

    return img


# 排除的窗口 class name：console / terminal 类（标题里可能含命令行参数）
_EXCLUDED_CLASSES = {
    'ConsoleWindowClass',         # cmd.exe / PowerShell 5.1
    'CASCADIA_HOSTING_WINDOW_CLASS',  # Windows Terminal
    'PseudoConsoleWindow',
    'Windows.UI.Core.CoreWindow',  # UWP system
}


def _find_window(needle: str) -> int | None:
    """模糊找窗口 hwnd（contains，不区分大小写）

    自动排除 console 类窗口（PowerShell / cmd / Terminal）— 它们的标题栏可能
    含 gaze 启动命令里的参数（"-w Doki"），会被错误 match。
    """
    needle_lower = needle.lower()
    for title, hwnd in list_windows():
        if needle_lower not in title.lower():
            continue
        # 排除 console 类
        if _HAS_PYWIN32:
            try:
                class_name = win32gui.GetClassName(hwnd)
                if class_name in _EXCLUDED_CLASSES:
                    continue
            except Exception:
                pass
        return hwnd
    return None


def get_foreground_window() -> tuple[str, int] | None:
    """获取当前前台活跃窗口（标题, hwnd）

    自动过滤 console 类（PowerShell/cmd），返回 None 让调用方 fallback。
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    # 过滤 console / 系统类窗口
    if _HAS_PYWIN32:
        try:
            cls = win32gui.GetClassName(hwnd)
            if cls in _EXCLUDED_CLASSES:
                return None
            # 也过滤 desktop / shell
            if cls in ('WorkerW', 'Progman', 'Shell_TrayWnd'):
                return None
        except Exception:
            pass

    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return None
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.strip()
    if not title:
        return None
    return (title, hwnd)


def list_windows() -> list[tuple[str, int]]:
    """列出所有可见窗口（标题非空）"""
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
    )

    results: list[tuple[str, int]] = []

    def callback(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title.strip():
                    results.append((title, hwnd))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return results


def perceptual_hash(image: Image.Image, hash_size: int = 8) -> str:
    """计算图片的 dhash (difference hash)，用于判断画面变化。

    返回二进制字符串 (长度 hash_size * hash_size)。
    """
    img = image.convert('L').resize((hash_size + 1, hash_size), Image.LANCZOS)
    pixels = list(img.getdata())

    bits = []
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits.append('1' if left > right else '0')

    return ''.join(bits)


def hamming_distance(h1: str, h2: str) -> int:
    """两个 hash 的汉明距离（不同位的数量）"""
    if len(h1) != len(h2):
        return max(len(h1), len(h2))
    return sum(c1 != c2 for c1, c2 in zip(h1, h2))

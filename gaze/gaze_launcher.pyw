"""gaze 启动器 — 弹窗选窗口

双击启动 → 列出当前所有可见窗口 → 你点哪个就截哪个 → 启动 gaze。

也支持「全屏共享」选项（不限定窗口）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

# 让 launcher 能 import gaze 的 capture 模块
GAZE_DIR = Path(__file__).parent
sys.path.insert(0, str(GAZE_DIR))

from capture.screen import list_windows  # noqa: E402


# 过滤这些窗口（启动器自身/系统窗口不该出现在选择列表里）
_HIDE_TITLES = [
    'Program Manager',
    'Microsoft Text Input Application',
    'gaze 启动器',
    'Settings',
]


def get_window_options() -> list[tuple[str, str | None]]:
    """返回 [(显示标题, gaze -w 参数), ...]

    第一项是"全屏"，其余是当前可见窗口（过滤掉系统/console 类）
    """
    windows = list_windows()
    # 去掉 console (PowerShell / cmd) 和系统窗口
    filtered = []
    for title, hwnd in windows:
        if any(skip in title for skip in _HIDE_TITLES):
            continue
        # console 标题往往含 "命令提示符" / cmd.exe / PowerShell
        if '命令提示符' in title or 'Windows PowerShell' in title:
            continue
        if not title.strip():
            continue
        filtered.append((title, title))

    # 前几项：预设模式
    options: list[tuple[str, str | None, str]] = [
        ('🔄  自动跟随前台窗口（切窗口不用重启 gaze，最聪明）', None, 'auto_window'),
        ('🔊  自动跟随 + 音频字幕（Whisper 转语音，外语视频/直播必备）', None, 'auto_window_audio'),
        ('📖  文字游戏快读模式（DDLC/galgame 1s 翻多页也跟得上）', None, 'text_fast'),
        ('🖥️  全屏共享（截整个屏幕，最通用）', None, 'normal'),
        ('🎬  浏览器看视频（全屏 + 自动裁掉地址栏/书签栏/任务栏）', None, 'browser_video'),
    ]
    for title, _ in filtered:
        # 截前 70 字符显示
        display = title if len(title) <= 70 else title[:67] + '...'
        # gaze -w 参数：取窗口标题前 20 字作为 contains 匹配关键词
        keyword = title[:20].strip()
        options.append((f'🪟  {display}', keyword, 'normal'))

    return options


def launch_gaze(window_arg: str | None, mode: str = 'normal'):
    """启动 gaze_local.py，可选 -w 参数指定窗口 + mode 控制 crop"""
    # 用当前进程的 python（pyw 启动器走的是 pythonw.exe，subprocess 起 python.exe）
    python_exe = sys.executable.replace('pythonw.exe', 'python.exe')
    script = str(GAZE_DIR / 'gaze_local.py')
    args = [python_exe, script, '-p', 'glm', '-i', '10', '--ocr-interval', '3']
    if window_arg:
        args.extend(['-w', window_arg])
    if mode == 'browser_video':
        # 浏览器看视频：裁掉上方 150px (地址栏+书签栏) + 下方 50px (任务栏)
        args.extend(['--crop-top', '150', '--crop-bottom', '50'])
    elif mode == 'auto_window':
        # 🔄 自动跟随前台窗口
        args.extend(['--auto-window'])
    elif mode == 'auto_window_audio':
        # 🔊 自动跟随 + 音频字幕
        args.extend(['--auto-window', '--audio'])
    elif mode == 'text_fast':
        # 📖 文字游戏快读模式
        args.extend(['--auto-window', '--text-fast'])

    CREATE_NEW_CONSOLE = 0x00000010
    subprocess.Popen(
        args,
        cwd=str(GAZE_DIR),
        creationflags=CREATE_NEW_CONSOLE,
    )


def main():
    root = tk.Tk()
    root.title('启动 gaze')
    root.geometry('640x500')

    # 设置中文字体
    try:
        from tkinter import font as tkfont
        default_font = tkfont.nametofont('TkDefaultFont')
        default_font.configure(family='Microsoft YaHei UI', size=10)
    except Exception:
        pass

    # 顶部说明
    header = tk.Label(
        root,
        text='🫧 选择要让 AI 看到的窗口',
        font=('Microsoft YaHei UI', 14, 'bold'),
        pady=15,
    )
    header.pack()

    sub = tk.Label(
        root,
        text='全屏 = 截整个屏幕（最通用）\n选窗口 = 只截那个窗口的内容（更隐私）',
        font=('Microsoft YaHei UI', 9),
        fg='gray',
    )
    sub.pack(pady=(0, 10))

    # 中间 listbox
    frame = tk.Frame(root)
    frame.pack(fill='both', expand=True, padx=20, pady=5)

    scrollbar = tk.Scrollbar(frame)
    scrollbar.pack(side='right', fill='y')

    listbox = tk.Listbox(
        frame,
        font=('Microsoft YaHei UI', 10),
        yscrollcommand=scrollbar.set,
        selectmode='single',
        activestyle='dotbox',
        height=15,
    )
    listbox.pack(side='left', fill='both', expand=True)
    scrollbar.config(command=listbox.yview)

    options = get_window_options()
    for opt in options:
        listbox.insert('end', opt[0])
    listbox.selection_set(0)
    listbox.see(0)

    # 底部按钮
    btn_frame = tk.Frame(root, pady=15)
    btn_frame.pack()

    def on_launch():
        sel = listbox.curselection()
        if not sel:
            messagebox.showwarning('提示', '请先选一项')
            return
        idx = sel[0]
        opt = options[idx]
        window_arg = opt[1]
        mode = opt[2] if len(opt) > 2 else 'normal'
        try:
            launch_gaze(window_arg, mode=mode)
            root.destroy()
        except Exception as e:
            messagebox.showerror('启动失败', f'{type(e).__name__}: {e}')

    def on_refresh():
        listbox.delete(0, 'end')
        nonlocal options
        options = get_window_options()
        for opt in options:
            listbox.insert('end', opt[0])
        listbox.selection_set(0)

    tk.Button(
        btn_frame,
        text='启动 gaze',
        command=on_launch,
        font=('Microsoft YaHei UI', 11),
        bg='#5dade2',
        fg='white',
        padx=20,
        pady=5,
        relief='flat',
    ).pack(side='left', padx=5)

    tk.Button(
        btn_frame,
        text='刷新窗口列表',
        command=on_refresh,
        font=('Microsoft YaHei UI', 10),
        padx=10,
        pady=5,
    ).pack(side='left', padx=5)

    tk.Button(
        btn_frame,
        text='取消',
        command=root.destroy,
        font=('Microsoft YaHei UI', 10),
        padx=10,
        pady=5,
    ).pack(side='left', padx=5)

    # Enter 键启动
    listbox.bind('<Double-Button-1>', lambda e: on_launch())
    root.bind('<Return>', lambda e: on_launch())
    root.bind('<Escape>', lambda e: root.destroy())

    root.mainloop()


if __name__ == '__main__':
    main()

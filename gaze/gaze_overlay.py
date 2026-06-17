"""gaze 实时浮窗 GUI — 边用电脑边能瞄一眼 gaze 抓到啥

设计：
- Tkinter 半透明无边框窗口，always on top
- 默认在右下角，可拖动
- 双击 = 折叠成小气泡 🫧
- 右键 = 退出（杀整个 gaze）
- 主循环通过共享 dict 写最新状态，浮窗 500ms 自拉

启动方式（gaze_local.py 自动调用，不直接 run）：
    from gaze_overlay import GazeOverlay
    state = {...}
    overlay = GazeOverlay(state)
    threading.Thread(target=overlay.run, daemon=True).start()
"""
from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from tkinter import font as tkfont


# 字体（统一 Microsoft YaHei UI 中文显示好）
FONT_FAMILY = 'Microsoft YaHei UI'


class GazeOverlay:
    """gaze 状态实时浮窗

    state dict 字段（由 gaze_local.py 写）：
        window:        当前活跃窗口名
        last_ocr:      最新一条 OCR 文本
        last_cap:      最新一条 caption 文本
        last_audio:    最新一条 audio 转写
        ocr_count:     累计 OCR push 次数
        cap_count:     累计 caption push 次数
        audio_count:   累计 audio push 次数
        last_status:   最后一次 push 状态（✓ / ✗ / ⏭噪音 / 🔒 黑名单）
        last_status_ts: 上次状态更新时间戳
        paused:        是否暂停（True = 黑名单跳过中）
    """

    def __init__(self, state: dict):
        self.state = state
        self.collapsed = False
        self.dragging = False
        self.drag_offset = (0, 0)

        self.root = tk.Tk()
        self.root.title('🫧 gaze')
        # 无边框 + always on top + 半透明
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.88)
        # 背景色：深灰偏紫（水母色调）
        self.root.configure(bg='#1a1a2e')

        # 默认位置：右下角
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w, h = 360, 180
        self.root.geometry(f'{w}x{h}+{sw - w - 20}+{sh - h - 60}')

        self._build_ui()
        self._bind_events()

        # 🪟 Win32: 让浮窗不抢前台焦点 + 不出现在 Alt+Tab
        # 这样 gaze 主循环用 GetForegroundWindow 不会把浮窗误认为前台 → 不触发套娃保护
        # 拖动 / 双击 / 右键菜单照常 work（NOACTIVATE 只阻止激活，不阻止鼠标事件）
        self.root.after(100, self._apply_nonactivate_style)

        # 启动定时更新
        self.root.after(500, self._update_loop)

    def _apply_nonactivate_style(self):
        """给浮窗顶层 hwnd 设 WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW（Win32 only）"""
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            user32 = ctypes.windll.user32
            hwnd = self.root.winfo_id()
            # 爬到顶层窗口（Tkinter 上 winfo_id 常返回 inner child）
            for _ in range(5):
                parent = user32.GetParent(hwnd)
                if not parent:
                    break
                hwnd = parent
            if hwnd:
                ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:
            pass  # 失败 silent

    def _build_ui(self):
        """构建 UI"""
        # 顶部标题栏（拖动手柄 + 健康指示灯 + 文字）
        self.title_bar = tk.Frame(self.root, bg='#16213e', height=22)
        self.title_bar.pack(side='top', fill='x')
        self.title_bar.pack_propagate(False)

        # R: 健康指示灯（左侧小圆点 ● 绿/黄/红/灰）
        self.health_dot = tk.Label(
            self.title_bar, text='●',
            font=(FONT_FAMILY, 12), bg='#16213e', fg='#888888',  # 灰色 = 未知
        )
        self.health_dot.pack(side='left', padx=(6, 2))

        self.title_label = tk.Label(
            self.title_bar, text='gaze · 拖动 / 双击折叠 / 右键菜单',
            font=(FONT_FAMILY, 8), bg='#16213e', fg='#8888aa',
        )
        self.title_label.pack(side='left', padx=2)

        # S: 右上角显示「Xs 前 ↑」上次有新 push
        self.last_push_label = tk.Label(
            self.title_bar, text='—',
            font=(FONT_FAMILY, 8), bg='#16213e', fg='#666688',
        )
        self.last_push_label.pack(side='right', padx=6)

        # 主体内容
        body = tk.Frame(self.root, bg='#1a1a2e')
        body.pack(side='top', fill='both', expand=True, padx=10, pady=6)

        # 状态行：当前窗口 + 状态
        self.status_label = tk.Label(
            body, text='● 启动中',
            font=(FONT_FAMILY, 10, 'bold'), bg='#1a1a2e', fg='#7fdbff',
            anchor='w', justify='left',
        )
        self.status_label.pack(fill='x', pady=(0, 4))

        # OCR 行
        ocr_row = tk.Frame(body, bg='#1a1a2e')
        ocr_row.pack(fill='x', pady=1)
        tk.Label(ocr_row, text='OCR', font=(FONT_FAMILY, 8, 'bold'),
                 bg='#1a1a2e', fg='#ffcc00', width=4, anchor='w').pack(side='left')
        self.ocr_label = tk.Label(
            ocr_row, text='—',
            font=(FONT_FAMILY, 9), bg='#1a1a2e', fg='#dddddd',
            anchor='w', justify='left', wraplength=300,
        )
        self.ocr_label.pack(side='left', fill='x', expand=True)

        # Caption 行
        cap_row = tk.Frame(body, bg='#1a1a2e')
        cap_row.pack(fill='x', pady=1)
        tk.Label(cap_row, text='CAP', font=(FONT_FAMILY, 8, 'bold'),
                 bg='#1a1a2e', fg='#7fff00', width=4, anchor='w').pack(side='left')
        self.cap_label = tk.Label(
            cap_row, text='—',
            font=(FONT_FAMILY, 9), bg='#1a1a2e', fg='#dddddd',
            anchor='w', justify='left', wraplength=300,
        )
        self.cap_label.pack(side='left', fill='x', expand=True)

        # Audio 行
        aud_row = tk.Frame(body, bg='#1a1a2e')
        aud_row.pack(fill='x', pady=1)
        tk.Label(aud_row, text='AUD', font=(FONT_FAMILY, 8, 'bold'),
                 bg='#1a1a2e', fg='#ff66cc', width=4, anchor='w').pack(side='left')
        self.aud_label = tk.Label(
            aud_row, text='—',
            font=(FONT_FAMILY, 9), bg='#1a1a2e', fg='#dddddd',
            anchor='w', justify='left', wraplength=300,
        )
        self.aud_label.pack(side='left', fill='x', expand=True)

        # 计数行
        self.count_label = tk.Label(
            body, text='Σ  OCR 0  ·  CAP 0  ·  AUD 0',
            font=(FONT_FAMILY, 8), bg='#1a1a2e', fg='#666688',
            anchor='w',
        )
        self.count_label.pack(fill='x', pady=(4, 0))

    def _bind_events(self):
        """事件绑定：拖动 / 双击折叠 / 右键弹菜单"""
        # T: 右键菜单
        self.menu = tk.Menu(self.root, tearoff=0,
                            bg='#16213e', fg='#dddddd',
                            activebackground='#7fdbff', activeforeground='#1a1a2e',
                            font=(FONT_FAMILY, 9))
        self.menu.add_command(label='⏸  暂停 gaze', command=self._on_pause)
        self.menu.add_command(label='▶  恢复 gaze', command=self._on_resume)
        self.menu.add_separator()
        self.menu.add_command(label='📸 立即推送一张高清快照', command=self._on_force_snap)
        self.menu.add_command(label='🔄 重置统计', command=self._on_reset_counters)
        self.menu.add_separator()
        self.menu.add_command(label='✖  退出 gaze', command=self._on_right_click)

        for w in [self.root, self.title_bar, self.title_label, self.health_dot,
                  self.last_push_label]:
            w.bind('<Button-1>', self._on_drag_start)
            w.bind('<B1-Motion>', self._on_drag_motion)
            w.bind('<Double-Button-1>', self._toggle_collapse)
            w.bind('<Button-3>', self._show_menu)

    def _show_menu(self, event):
        """右键弹出菜单"""
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _on_pause(self):
        """T: 用户主动暂停"""
        self.state['user_paused'] = True

    def _on_resume(self):
        """T: 用户主动恢复"""
        self.state['user_paused'] = False

    def _on_force_snap(self):
        """T: 立即推一张高清快照（gaze 主循环检测到 force_snap=True 立刻推）"""
        self.state['force_snap'] = True

    def _on_reset_counters(self):
        """T: 重置计数（保留浮窗在运行，只清归零）"""
        self.state['ocr_count'] = 0
        self.state['cap_count'] = 0
        self.state['audio_count'] = 0
        self.state['push_fail_count'] = 0
        self.state['push_success_count'] = 0

    def _on_drag_start(self, event):
        self.dragging = True
        self.drag_offset = (event.x_root - self.root.winfo_x(),
                            event.y_root - self.root.winfo_y())

    def _on_drag_motion(self, event):
        if self.dragging:
            new_x = event.x_root - self.drag_offset[0]
            new_y = event.y_root - self.drag_offset[1]
            self.root.geometry(f'+{new_x}+{new_y}')

    def _toggle_collapse(self, event=None):
        """双击折叠/展开"""
        if self.collapsed:
            # 展开
            self.root.geometry('360x180')
            self.collapsed = False
        else:
            # 折叠成 60x22 小气泡
            self.root.geometry('60x22')
            self.collapsed = True

    def _on_right_click(self, event=None):
        """右键 = 退出 gaze（设置 state['quit']=True 让主循环看到）"""
        self.state['quit'] = True
        try:
            self.root.destroy()
        except Exception:
            pass
        # 不直接 os._exit — 让主循环自己优雅退出

    def _truncate(self, text: str, max_len: int = 80) -> str:
        """截断长文本"""
        if not text:
            return '—'
        # 去掉前缀
        if text.startswith('[屏上文字] '):
            text = text[len('[屏上文字] '):]
        elif text.startswith('[音频] '):
            text = text[len('[音频] '):]
        text = text.replace('\n', ' ').strip()
        if len(text) <= max_len:
            return text
        return text[:max_len - 1] + '…'

    def _update_loop(self):
        """500ms 拉一次 state，刷新 UI"""
        try:
            s = self.state

            # R: 健康指示灯 — 根据 push 成功率
            health_color, health_tip = self._compute_health(s)
            self.health_dot.config(fg=health_color)

            # S: 「Xs 前 ↑」上次有新 push
            last_push_ts = s.get('last_push_ts', 0)
            if last_push_ts:
                age = int(time.time() - last_push_ts)
                if age < 60:
                    self.last_push_label.config(text=f'{age}s 前 ↑', fg='#7fdbff')
                elif age < 3600:
                    self.last_push_label.config(text=f'{age // 60}m 前 ↑',
                                                fg='#ffcc66' if age > 120 else '#7fdbff')
                else:
                    self.last_push_label.config(text=f'{age // 3600}h 前 ↑', fg='#ff6666')
            else:
                self.last_push_label.config(text='—', fg='#666688')

            if self.collapsed:
                # 折叠状态只显示一个 🫧 + 心跳点
                pass
            else:
                # 状态栏
                window = s.get('window') or '(全屏)'
                if s.get('user_paused'):
                    self.status_label.config(text=f'⏸ 用户暂停', fg='#ffcc66')
                elif s.get('paused'):
                    self.status_label.config(text=f'🔒 黑名单：{window[:25]}', fg='#ff8866')
                elif s.get('error'):
                    self.status_label.config(text=f'⚠️ {s.get("error", "")[:30]}', fg='#ff6666')
                elif s.get('cap_circuit_open'):
                    self.status_label.config(text=f'⏸ caption 限流中 ({window[:20]})', fg='#ffcc66')
                else:
                    # V: 显示 mode + window
                    mode = s.get('mode', '')
                    if mode and '文字游戏' in mode:
                        self.status_label.config(text=f'📖 {window[:28]}', fg='#ffaa66')
                    else:
                        self.status_label.config(text=f'● {window[:30]}', fg='#7fdbff')

                # OCR / Caption / Audio 最新一条
                self.ocr_label.config(text=self._truncate(s.get('last_ocr', '')))
                self.cap_label.config(text=self._truncate(s.get('last_cap', '')))
                self.aud_label.config(text=self._truncate(s.get('last_audio', '')))

                # 计数
                self.count_label.config(
                    text=f'Σ  OCR {s.get("ocr_count", 0)}  '
                         f'·  CAP {s.get("cap_count", 0)}  '
                         f'·  AUD {s.get("audio_count", 0)}  '
                         f'·  ✗ {s.get("push_fail_count", 0)}'
                )
        except Exception:
            pass  # silent fail，浮窗坏了不要影响主循环

        self.root.after(500, self._update_loop)

    def _compute_health(self, s: dict) -> tuple[str, str]:
        """R: 根据近期 push 成功率算健康灯颜色

        - 绿 (#7fff7f): 最近 10 次 push 成功率 >= 80%，且有近期 push
        - 黄 (#ffcc66): 成功率 50-80%，或最近 60s 没 push
        - 红 (#ff6666): 成功率 < 50%，或最近 5min 没任何 push
        - 灰 (#888888): 还没数据
        """
        last_push_ts = s.get('last_push_ts', 0)
        if not last_push_ts:
            return ('#888888', 'no data')

        age = time.time() - last_push_ts
        success = s.get('push_success_count', 0)
        fail = s.get('push_fail_count', 0)
        total = success + fail
        if total == 0:
            return ('#888888', 'no data')
        rate = success / total

        if age > 300:
            return ('#ff6666', f'死寂 {int(age)}s')
        if age > 60:
            return ('#ffcc66', f'静默 {int(age)}s')
        if rate < 0.5:
            return ('#ff6666', f'失败率 {int((1-rate)*100)}%')
        if rate < 0.8:
            return ('#ffcc66', f'失败率 {int((1-rate)*100)}%')
        return ('#7fff7f', f'成功率 {int(rate*100)}%')

    def run(self):
        """启动 Tkinter mainloop（在独立 thread 调用）"""
        try:
            self.root.mainloop()
        except Exception:
            pass


def start_overlay_in_thread(state: dict):
    """gaze_local.py 用这个起浮窗（独立 daemon thread）"""
    import threading

    def _worker():
        try:
            overlay = GazeOverlay(state)
            overlay.run()
        except Exception as e:
            print(f"⚠️ overlay failed: {type(e).__name__}: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


if __name__ == '__main__':
    # 自测：模拟 state 数据看浮窗显示效果
    import time
    import threading

    test_state = {
        'window': 'Doki Doki Literature Club',
        'last_ocr': '莫妮卡：我们继续刚才的话题吧',
        'last_cap': '莫妮卡坐在桌前，背景是夜晚的教室',
        'last_audio': '',
        'ocr_count': 12,
        'cap_count': 3,
        'audio_count': 0,
        'paused': False,
    }

    def _mock_update():
        n = 0
        while True:
            n += 1
            test_state['ocr_count'] = n
            test_state['last_ocr'] = f'测试 OCR 文本 #{n} - 莫妮卡说了点什么'
            test_state['last_cap'] = f'画面变化 #{n // 3} - 角色表情切换'
            time.sleep(2)

    threading.Thread(target=_mock_update, daemon=True).start()
    overlay = GazeOverlay(test_state)
    overlay.run()

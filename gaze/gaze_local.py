"""gaze 本地主程序 — 给你的 AI 借一只眼睛

Usage:
    # 列出所有窗口（找 DDLC / 浏览器之类的标题）
    python gaze_local.py --list-windows

    # 跑 DDLC 模式（10s 截一次，只截 DDLC 窗口）
    python gaze_local.py --provider glm --interval 10 --window "Doki Doki"

    # 看电影模式（5s 截一次，全屏）
    python gaze_local.py --provider qwen --interval 5

    # 详细解说风格（默认是短弹幕）
    python gaze_local.py --style detailed --window "Bilibili"

按 Ctrl+C 停。

Caption 写到 ~/.gaze/logs/*.jsonl，每次启动一份。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows 终端 GBK 默认无法输出 BMP 外 emoji (📖 🫧 等)，强制 stdout UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from dotenv import load_dotenv

# 加载 .env（先找当前目录再找 gaze 目录）
GAZE_DIR = Path(__file__).parent
load_dotenv(GAZE_DIR / '.env')

from captioner import GLMCaptioner, QwenCaptioner, DoubaoCaptioner, MockCaptioner  # noqa: E402
from capture import screenshot, perceptual_hash, hamming_distance, list_windows, crop_borders, get_foreground_window  # noqa: E402
from capture.screen import _find_window  # noqa: E402
from capture.ocr import ocr_image, diff_new_text, join_text_lines  # noqa: E402

import re  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
from io import BytesIO  # noqa: E402


# 清洗 caption / OCR 的噪音模式
_NOISE_REGEXES = [
    re.compile(r'https?://\S+'),                              # URL
    re.compile(r'xiaohongshu\.com/\S+'),                       # 小红书 URL
    re.compile(r'\S*xsec_token=\S+'),                          # 小红书 token
    re.compile(r'\S*xsec_source=\S+'),                         # 小红书 source
    re.compile(r'\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}'),         # 视频进度条 "00:18/01:35"
    re.compile(r'\[?\s*www\.\S+\s*[－—\-]?\s*'),                # chrome 全屏提示开头 "[www.xxx.com－"
    re.compile(r'\[?\s*若要退出全屏模式[^\n/]*\]?'),             # chrome 全屏提示中段
    re.compile(r'\bEsc\b'),                                    # Esc 单字（任意位置）
    re.compile(r'CWing[\s\S]{0,30}'),                          # 视频特定噪音
    re.compile(r'\.{3,}'),                                     # 单独的省略号 "..."
    re.compile(r'…+'),                                          # 中文省略号
]

# 浏览器书签栏常见关键词（直接 strip，避免 OCR 把书签栏文字当成内容推出去）
_BOOKMARK_KEYWORDS = [
    # 正常标题
    '所有书签', 'Netflix', 'Nintendo Account',
    '百度一下，你就知道', 'ChatGPT', 'LOFTER', '我的首页 微博',
    'Help needed with cod', 'Claude',
    # 哔哩哔哩 + OCR 错字变体
    '哔哩哔哩', '哗哩哗哩', '哔哩哗哩', '哗哩哔哩',
    'bilibili', '哗哩哗哩 (', '哔哩哔哩 (', '哔哩哔哩（', '哗哩哗哩（',
    # 被书签栏截断的常见尾词
    'Ninte', 'Ninten',  # Nintendo 截断
    'Netfli',  # Netflix 截断
    'ChatG', 'Clau',
    # 浏览器收藏栏空白符号 / 间隔
    '/ 。 /', '/ ） /', '（°-）',
    # 微博 / 等
    '我的首页', '微博',
]

# chrome UI 残留信号（命中即触发噪音处理）
_CHROME_UI_SIGNALS = [
    re.compile(r'[（(]\s*[°○\.]\s*[\-－]\s*[°○\.]?\s*[）)]'),  # 哔哩哔哩书签 emoji (°-°) / (°-) / 各种 OCR 变体
    re.compile(r'/\s*。\s*/'),                                # tab 序号分隔 "/。/"
    re.compile(r'Ninte\b'),                                   # Nintendo 截断
    re.compile(r'Netfli\b'),
]


def _clean_caption(text: str, min_keep: int = 3) -> str | None:
    """清洗 caption / OCR 文本里的噪音

    1. 移除 URL / token / 视频进度条 / chrome 提示
    2. 移除浏览器书签栏常见关键词
    3. 合并多余的 / 和空白
    4. 清洗后 < min_keep 字符返回 None（跳过 push）

    min_keep=3 让 DDLC 角色名（"纱世里" / "莫妮卡"）通过，但纯单字符乱码过滤掉。

    Returns:
        清洗后的字符串；全是噪音则返回 None
    """
    if not text:
        return None

    # 1) 移除前缀 "[屏上文字] " 暂存
    prefix = ''
    if text.startswith('[屏上文字] '):
        prefix = '[屏上文字] '
        text = text[len(prefix):]
    elif text.startswith('[屏上文字]'):
        prefix = '[屏上文字] '
        text = text[len('[屏上文字]'):].lstrip()

    # 2) 应用 regex 移除
    for rx in _NOISE_REGEXES:
        text = rx.sub('', text)

    # 3) 移除书签栏关键词
    for kw in _BOOKMARK_KEYWORDS:
        text = text.replace(kw, '')

    # 4) 合并 / 和空白
    text = re.sub(r'(\s*/\s*)+', ' / ', text)  # 多个 / 合并
    text = re.sub(r'\s{2,}', ' ', text)         # 多空白合并
    text = text.strip(' /\t\n')                  # 头尾 / 空白

    # 5) 检查剩余内容长度
    bare = re.sub(r'[/\s]+', '', text)
    if len(bare) < min_keep:
        return None  # 全是噪音

    # 6) chrome UI 信号检测：含多个 = 整段是 chrome 顶栏 → 判 None
    chrome_ui_hits = sum(1 for rx in _CHROME_UI_SIGNALS if rx.search(text))
    if chrome_ui_hits >= 1:
        # 先把命中的 chrome UI signal strip 掉
        for rx in _CHROME_UI_SIGNALS:
            text = rx.sub('', text)
        # 再次合并 / 空白
        text = re.sub(r'(\s*/\s*)+', ' / ', text)
        text = re.sub(r'\s{2,}', ' ', text)
        text = text.strip(' /\t\n.…')
        bare = re.sub(r'[/\s…\.]+', '', text)
        # 含 chrome signal 的内容要求更高 min_keep（10 字符），过滤掉残留的破碎 tab 标题
        if len(bare) < 10:
            return None

    return prefix + text





# ─── window 黑名单：含这些关键词的窗口标题永不截 ───────────────
# 命中 = 整个 gaze 那一轮跳过（不截/不推），保护隐私
_WINDOW_BLACKLIST = [
    # 即时通讯（聊天可能含私密内容）
    '微信', 'WeChat', 'QQ', 'TIM', 'Telegram', 'Discord',
    # 银行 / 支付
    '招商银行', '工商银行', '建设银行', '农业银行', '中国银行', '银行',
    '支付宝', 'Alipay', 'Wallet',
    # 密码管理器
    '1Password', 'Bitwarden', 'KeePass', 'LastPass',
    # 邮件（隐私）
    'Outlook', 'Mail',
    # cmd / 工具 (双保险)
    '命令提示符', 'Windows Terminal',
    # gaze 自己 (避免无限套娃)
    'gaze_local', 'gaze_launcher',
]


def _is_window_blacklisted(title: str) -> bool:
    """检测窗口标题是否在黑名单"""
    if not title:
        return False
    for kw in _WINDOW_BLACKLIST:
        if kw.lower() in title.lower():
            return True
    return False


# ─── window 名归一化 — 浏览器切 tab 不让 memory store key 分裂 ──
# 规则：标题里含这些"应用尾巴"时，统一成简短稳定 key
_APP_SUFFIX_MAP = [
    # (匹配子串, 归一化后的 key)
    ('Google Chrome', 'Chrome'),
    ('Microsoft Edge', 'Edge'),
    ('Mozilla Firefox', 'Firefox'),
    ('Opera', 'Opera'),
    ('Brave', 'Brave'),
    ('Visual Studio Code', 'VSCode'),
    ('网易云音乐', '网易云音乐'),
    ('QQ音乐', 'QQ音乐'),
    ('Spotify', 'Spotify'),
    ('Bilibili', 'Bilibili'),
    ('哔哩哔哩', 'Bilibili'),
    ('哗哩哗哩', 'Bilibili'),
    ('Twitter', 'Twitter'),
    ('Discord', 'Discord'),
    ('YouTube', 'YouTube'),
    ('Doki Doki', 'DDLC'),
    ('Doki', 'DDLC'),
]


def normalize_window_key(title: str | None) -> str:
    """把窗口标题归一化成稳定的 key（解决浏览器切 tab 标题变化问题）

    - 浏览器 → "Chrome" / "Edge" / "Firefox" (不管开了哪个 tab)
    - 已知 app 名 → 简短 key
    - 其他 → 原标题前 30 字符
    """
    if not title:
        return 'fullscreen'
    for needle, key in _APP_SUFFIX_MAP:
        if needle.lower() in title.lower():
            return key
    return title[:30].strip() or 'fullscreen'


# 命中其一就过滤（_filter_console_noise 用），避免把 console / 开发环境内容当成游戏画面误推
_CONSOLE_NOISE_PATTERNS = (
    '命令提示符', 'Windows PowerShell', 'Microsoft Windows [版本',
    'gaze_local', 'mcp-project', 'mcp_project',
    'python.exe', 'Python312', 'AppData\\Local\\Programs',
    'AppData/Local/Programs',
    'ocr-interval', 'PYTHONIOENCODING',
    'Invoke-RestMethod', 'Get-Process', 'Get-ChildItem',
    'cd C:\\', 'cd /c/',
    'PS C:\\', 'cmd.exe',
    '> Stop-Process',
)


def _is_console_noise(text: str) -> bool:
    """检测 caption / OCR 文本是不是 console / 开发环境内容（应该 skip 不推）"""
    if not text:
        return False
    lower = text.lower()
    for pattern in _CONSOLE_NOISE_PATTERNS:
        if pattern.lower() in lower:
            return True
    return False


# share 版：所有 ssh/VPS 路径都从 env var 读，--no-push 一键跑离线
_SSH_HOST = os.environ.get('GAZE_SSH_HOST', 'your-vps')
_VPS_PUSH_SCRIPT = os.environ.get('GAZE_VPS_SCRIPT', '/root/mcp-memory-server/push_caption.py')
_VPS_SNAP_PATH = os.environ.get('GAZE_VPS_SNAP_PATH', '/tmp/gaze_latest.jpg')
_NO_PUSH = False  # main() 会根据 --no-push 翻起来


def push_snap_async(img, ssh_host: str = None, quality: int = 85):
    """异步 scp 一张高清 JPG 到 VPS (供 AI 主动调拿高清图用)

    后台 thread 跑，不阻塞主循环。失败 silent。
    """
    if _NO_PUSH:
        return
    ssh_host = ssh_host or _SSH_HOST

    def _worker():
        try:
            buf = BytesIO()
            img.convert('RGB').save(buf, format='JPEG', quality=quality)
            jpg_bytes = buf.getvalue()
            p = subprocess.Popen(
                ['ssh', '-o', 'ConnectTimeout=5', ssh_host,
                 f'cat > {_VPS_SNAP_PATH}'],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            p.communicate(input=jpg_bytes, timeout=20)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


def push_to_vps(entry: dict, ssh_host: str = None, retries: int = 2) -> tuple[bool, str]:
    """SSH 推 caption 到 VPS（写入 memories.json 的 _realtime:* keys）。

    entry: {'caption': str, 'ts': str, 'source': 'ocr'|'cap'|'audio', 'window': str}
    返回 (success, message)

    带重试（SSH 偶尔 timeout）。
    --no-push 模式下直接 short-circuit（caption 只写本地 jsonl）。
    自动过滤：
    - console noise（命令行/开发环境关键词）→ skip
    - 屏幕噪音（URL/token/书签栏/视频进度条/chrome 提示）→ clean，全是噪音则 skip
    """
    if _NO_PUSH:
        return (False, 'no_push_mode')
    ssh_host = ssh_host or _SSH_HOST
    # ★ 过滤 console / 开发环境噪音
    caption = entry.get('caption', '')
    if _is_console_noise(caption):
        return (False, 'console_noise_skipped')

    # ★ 清洗屏幕噪音（URL/token/书签栏/视频进度条等）
    cleaned = _clean_caption(caption)
    if cleaned is None:
        return (False, 'all_noise_skipped')
    if cleaned != caption:
        # 用 clean 后的版本替换 push 内容
        entry = dict(entry)
        entry['caption'] = cleaned

    payload = json.dumps(entry, ensure_ascii=False)
    last_err = 'no attempt'
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=8',
                 '-o', 'ServerAliveInterval=5',
                 ssh_host,
                 f'python3 {_VPS_PUSH_SCRIPT}'],
                input=payload.encode('utf-8'),
                capture_output=True,
                timeout=15,
            )
            if result.returncode == 0:
                return (True, result.stdout.decode('utf-8', errors='replace').strip())
            else:
                last_err = result.stderr.decode('utf-8', errors='replace').strip() or f"exit {result.returncode}"
        except subprocess.TimeoutExpired:
            last_err = f'timeout (attempt {attempt+1})'
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(0.5)
    return (False, last_err)


PROVIDERS = {
    'mock': MockCaptioner,  # 不需要 API key，验证 pipeline 用
    'glm': GLMCaptioner,
    'qwen': QwenCaptioner,
    'doubao': DoubaoCaptioner,
}

LOG_DIR = Path.home() / '.gaze' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)


def run(
    provider_name: str,
    interval: int,
    window: str | None,
    style: str = 'danmu',
    hash_threshold: int = 4,
    max_static_skips: int = 3,
    ocr_enabled: bool = True,
    ocr_interval: float = 2.0,
    crop_top: int = 0,
    crop_bottom: int = 0,
    ocr_max_size: int = 1920,
    auto_window: bool = False,
    audio_enabled: bool = False,
    audio_model: str = 'tiny',
    overlay_enabled: bool = True,
    text_fast: bool = False,
):
    """主循环：双通道
    - 快通道 (每 ocr_interval 秒): 本地 OCR 抓屏幕文字 → diff → 新文字立刻推
    - 慢通道 (每 interval 秒): vision API 抓画面 caption → 推

    OCR 跟文字变化跑（DDLC 这种节奏快、信息核心是对话），
    caption 跟画面氛围跑（场景切换 / BGM 转换等）。
    """
    if provider_name not in PROVIDERS:
        print(f"未知 provider: {provider_name}（可选: {list(PROVIDERS.keys())}）")
        sys.exit(1)

    ProviderCls = PROVIDERS[provider_name]
    try:
        provider = ProviderCls()
    except ValueError as e:
        print(f"\n❌ {e}\n")
        print(f"提示：在 {GAZE_DIR / '.env'} 填入对应的 API key（参考 .env.example）")
        sys.exit(1)

    log_file = LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{provider_name}.jsonl"

    # V: text_fast 模式（文字游戏快读）— capture 0.3s + 关 caption
    if text_fast:
        capture_interval = 0.3
        ocr_enabled = True  # 强制开
        caption_enabled = False
        mode_label = '📖 文字游戏快读'
    else:
        capture_interval = max(0.5, ocr_interval)
        caption_enabled = True
        mode_label = '🫧 普通'

    print(f"┌─ gaze v3 (异步 OCR + Caption + Audio + 浮窗) ───")
    print(f"│ provider:        {provider.name}")
    print(f"│ 模式:            {mode_label}")
    print(f"│ capture interval: {capture_interval}s  (主循环截屏频率)")
    print(f"│ caption interval: {interval}s  (caption thread)")
    print(f"│ OCR enabled:     {ocr_enabled} (异步 worker, max_size={ocr_max_size})")
    print(f"│ Caption enabled: {caption_enabled}")
    print(f"│ Audio enabled:   {audio_enabled} (whisper {audio_model})")
    print(f"│ Overlay (浮窗):  {overlay_enabled}")
    print(f"│ Auto-window:     {auto_window}")
    print(f"│ window:          {window or '(全屏)'}")
    print(f"│ crop top/bottom: {crop_top}/{crop_bottom}")
    print(f"│ style:           {style}")
    print(f"│ log:             {log_file}")
    print(f"└────────────────────────────────────")
    print(f"按 Ctrl+C 停止\n")

    last_hash: str | None = None
    prev_texts: list[str] = []
    prev_texts_lock = threading.Lock()
    next_snap_push_time = 0.0  # 下次推高清图给 VPS 的时间
    SNAP_INTERVAL = 30           # 每 30s 推一张高清图
    cap_success = 0
    ocr_pushes = 0
    audio_pushes = 0
    fallback_announced = False
    # 用于 caption thread 跟主循环通信的共享状态
    cap_state = {
        'last_caption': None,   # 上一次 caption 文本 (给 B 用)
        'frame_history': [],    # 最近几帧 PIL.Image (给 D 用)
        'stop': False,
    }

    # V: OCR worker thread 共享状态（主循环写 pending_*，worker 读取最新）
    ocr_state = {
        'pending_img': None,
        'pending_window': None,
        'pending_hash': None,
        'last_ocr_hash': None,  # worker 记忆：上次 OCR 过的 hash
        'pending_ts': 0.0,       # 主循环写入时间
        'stop': False,
    }
    ocr_state_lock = threading.Lock()

    # 🫧 E + R/S/T: 实时浮窗共享状态
    overlay_state = {
        'window': window or '(全屏)',
        'last_ocr': '',
        'last_cap': '',
        'last_audio': '',
        'ocr_count': 0,
        'cap_count': 0,
        'audio_count': 0,
        'paused': False,                # 黑名单跳过
        'user_paused': False,           # T: 用户主动暂停（右键菜单）
        'error': '',
        'quit': False,                  # 右键退出 = 这里被设 True
        'force_snap': False,            # T: 用户点"立即推快照"
        'last_push_ts': 0.0,            # S: 最后一次成功 push 的时间戳（epoch sec）
        'push_success_count': 0,        # R: 累计成功 (用于健康灯)
        'push_fail_count': 0,           # R: 累计失败 (用于健康灯)
        'cap_circuit_open': False,      # N: caption circuit breaker 状态
        'mode': mode_label,             # V: 显示当前模式（📖 文字游戏快读 / 🫧 普通）
    }
    if overlay_enabled:
        try:
            from gaze_overlay import start_overlay_in_thread
            start_overlay_in_thread(overlay_state)
            print("🫧 浮窗已启动（右下角，可拖动；双击折叠，右键退出）")
        except Exception as e:
            print(f"⚠️ 浮窗启动失败（不影响主程序）: {type(e).__name__}: {e}")

    # 📖 V: 启动 OCR 独立 thread — 主循环高频写 pending_img，worker 永远 OCR 最新一帧
    # 这是文字游戏 1s 翻多页能跟得上的关键：OCR 不再卡主循环
    def _ocr_loop():
        nonlocal ocr_pushes
        while not ocr_state['stop']:
            time.sleep(0.05)  # 50ms 心跳：尽量快地检查新帧
            if ocr_state['stop']:
                break

            # T: 用户暂停跳过
            if overlay_state.get('user_paused'):
                continue

            with ocr_state_lock:
                img = ocr_state.get('pending_img')
                hash_ = ocr_state.get('pending_hash')
                window_ = ocr_state.get('pending_window')
                last_hash = ocr_state.get('last_ocr_hash')

            if img is None:
                continue
            # 画面跟上次 OCR 完全一样 → 跳过
            if hash_ and hash_ == last_hash:
                continue

            try:
                curr_texts = ocr_image(img, max_size=ocr_max_size)
                with prev_texts_lock:
                    new_lines = diff_new_text(prev_texts, curr_texts)
                # 标记这张已 OCR
                ocr_state['last_ocr_hash'] = hash_

                if new_lines:
                    joined = join_text_lines(new_lines, max_len=200)
                    now_iso = datetime.now().isoformat()
                    ts = datetime.now().strftime('%H:%M:%S')
                    push_ok, push_msg = push_to_vps({
                        'source': 'ocr',
                        'caption': f'[屏上文字] {joined}',
                        'ts': now_iso,
                        'window': window_ or 'fullscreen',
                    })
                    if push_ok:
                        tag = '→'
                    elif push_msg == 'console_noise_skipped':
                        tag = '⏭噪音'
                    else:
                        tag = f'×{push_msg[:15]}'
                    print(f"  [{ts}] [OCR] {joined[:80]}  {tag}")
                    ocr_pushes += 1

                    # 🫧 E + R/S: 写浮窗
                    overlay_state['last_ocr'] = joined
                    if push_ok:
                        overlay_state['ocr_count'] += 1
                        overlay_state['push_success_count'] += 1
                        overlay_state['last_push_ts'] = time.time()
                    elif push_msg != 'console_noise_skipped' and push_msg != 'all_noise_skipped':
                        overlay_state['push_fail_count'] += 1

                    with log_file.open('a', encoding='utf-8') as f:
                        f.write(json.dumps({
                            'ts': now_iso, 'source': 'ocr',
                            'text': joined, 'lines': new_lines,
                            'window': window_, 'push_ok': push_ok,
                        }, ensure_ascii=False) + '\n')

                # 更新 prev_texts 给 caption thread 用（list 是 mutable，直接 clear+extend）
                with prev_texts_lock:
                    prev_texts.clear()
                    prev_texts.extend(curr_texts)
            except Exception as e:
                print(f"  ⚠️ OCR err: {type(e).__name__}: {e}")

    if ocr_enabled:
        ocr_thread = threading.Thread(target=_ocr_loop, daemon=True)
        ocr_thread.start()
        print("📖 OCR worker 异步启动（主循环不再卡 OCR）")
    else:
        ocr_thread = None

    # 🎨 启动 caption 独立 thread（A: 不阻塞 OCR 主循环）
    # N: caption circuit breaker 状态
    cap_fail_streak = 0
    cap_circuit_until = 0.0   # epoch sec — 在这之前不再尝试
    # K: 自适应 interval 状态
    static_streak = 0          # 连续静止帧数
    current_interval = float(interval)

    def _caption_loop():
        nonlocal cap_fail_streak, cap_circuit_until, static_streak, current_interval
        last_hash_local: str | None = None
        while not cap_state['stop']:
            time.sleep(current_interval)
            if cap_state['stop']:
                break

            # T: 用户暂停 → 跳过这一轮（不写浮窗 last_cap，保留旧的）
            if overlay_state.get('user_paused'):
                continue

            # N: circuit breaker 开着的话直接跳
            now_sec = time.time()
            if now_sec < cap_circuit_until:
                overlay_state['cap_circuit_open'] = True
                continue
            overlay_state['cap_circuit_open'] = False

            frames = list(cap_state.get('frame_history', []))
            if not frames:
                continue

            # 当前最新帧
            img_now = frames[-1]
            cur_hash = cap_state.get('last_hash')

            # K: 画面静止时不仅跳过，还**延长**下一轮 sleep
            if last_hash_local and cur_hash and \
               hamming_distance(cur_hash, last_hash_local) < hash_threshold:
                static_streak += 1
                # 连续静止 3 次 = interval × 1.5；6 次 = × 2.5；上限 30s
                if static_streak >= 3:
                    current_interval = min(30.0, float(interval) * (1.5 + (static_streak - 3) * 0.3))
                continue
            else:
                # 画面动了 → 重置 interval
                static_streak = 0
                current_interval = float(interval)

            try:
                with prev_texts_lock:
                    recent_ocr_texts = list(prev_texts[-6:]) if prev_texts else None

                # B: 上下文化 — 传上一次 caption 给 prompt
                last_cap = cap_state.get('last_caption')
                if last_cap and hasattr(provider, 'caption_with_context'):
                    caption = provider.caption_with_context(
                        img_now, frames=frames, recent_ocr=recent_ocr_texts,
                        last_caption=last_cap, style=style,
                    )
                elif len(frames) >= 2 and hasattr(provider, 'caption_multi_frame'):
                    # D: 多帧融合
                    caption = provider.caption_multi_frame(
                        frames=frames, recent_ocr=recent_ocr_texts,
                        last_caption=last_cap, style=style,
                    )
                else:
                    caption = provider.caption(img_now, style=style, recent_ocr=recent_ocr_texts)

                cap_state['last_caption'] = caption

                now_iso = datetime.now().isoformat()
                ts = datetime.now().strftime('%H:%M:%S')
                aw = cap_state.get('last_window')
                push_ok, push_msg = push_to_vps({
                    'source': 'cap',
                    'caption': caption,
                    'ts': now_iso,
                    'window': aw or 'fullscreen',
                })
                tag = '→' if push_ok else ('⏭噪音' if push_msg == 'console_noise_skipped' else f'×{push_msg[:15]}')
                print(f"  [{ts}] [CAP] {caption}  {tag}")

                # 🫧 E + R/S: 写浮窗
                overlay_state['last_cap'] = caption
                if push_ok:
                    overlay_state['cap_count'] += 1
                    overlay_state['push_success_count'] += 1
                    overlay_state['last_push_ts'] = time.time()
                elif push_msg != 'console_noise_skipped' and push_msg != 'all_noise_skipped':
                    overlay_state['push_fail_count'] += 1

                with log_file.open('a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        'ts': now_iso, 'source': 'cap',
                        'caption': caption, 'provider': provider.name,
                        'window': aw, 'image_hash': cur_hash,
                        'push_ok': push_ok,
                    }, ensure_ascii=False) + '\n')

                last_hash_local = cur_hash
                # N: 这次成功 → 重置失败计数
                cap_fail_streak = 0
            except Exception as e:
                ts = datetime.now().strftime('%H:%M:%S')
                err_str = str(e)[:60]
                print(f"  [{ts}] ❌ CAP API err: {type(e).__name__}: {err_str}")
                # N: circuit breaker — 连续 3 次失败暂停 30s
                cap_fail_streak += 1
                if cap_fail_streak >= 3:
                    cap_circuit_until = time.time() + 30
                    print(f"  [{ts}] ⏸ caption 限流 / 连续 {cap_fail_streak} 次失败 → 暂停 30s")
                    overlay_state['cap_circuit_open'] = True

    if caption_enabled:
        cap_thread = threading.Thread(target=_caption_loop, daemon=True)
        cap_thread.start()
    else:
        cap_thread = None
        print("📖 caption 关闭（文字游戏模式跟不上 + 抢 CPU）")

    # 🔊 启动音频转写 (独立 thread)
    audio_transcriber = None
    if audio_enabled:
        try:
            from capture.audio import AudioTranscriber

            def _on_audio_text(text: str, ts_iso: str):
                nonlocal audio_pushes
                # 沿用当前 window 信息（auto_window 模式下用前台窗口名）
                _aw = normalize_window_key(window) if window else None
                if auto_window:
                    fg = get_foreground_window()
                    if fg:
                        _aw = normalize_window_key(fg[0])
                push_ok, _msg = push_to_vps({
                    'source': 'audio',
                    'caption': f'[音频] {text}',
                    'ts': ts_iso,
                    'window': _aw or 'fullscreen',
                })
                audio_pushes += 1
                ts_short = ts_iso[11:19] if len(ts_iso) > 19 else ts_iso
                print(f"  [{ts_short}] [AUD] {text[:80]}  {'→' if push_ok else '×'}")

                # 🫧 E + R/S: 写浮窗
                overlay_state['last_audio'] = text
                if push_ok:
                    overlay_state['audio_count'] += 1
                    overlay_state['push_success_count'] += 1
                    overlay_state['last_push_ts'] = time.time()
                else:
                    overlay_state['push_fail_count'] += 1

            audio_transcriber = AudioTranscriber(
                model_size=audio_model,
                chunk_seconds=8.0,
                language='zh',  # 中文优先，'auto' 也行但慢
                on_text=_on_audio_text,
            )
            audio_transcriber.start()
        except Exception as e:
            print(f"⚠️ 音频转写启动失败: {type(e).__name__}: {e}")
            audio_transcriber = None

    while True:
        try:
            # 🫧 E: 检测浮窗右键退出
            if overlay_state.get('quit'):
                print(f"\n[gaze] 浮窗右键退出")
                raise KeyboardInterrupt

            # T: 用户主动暂停 → sleep 后跳过这一轮
            if overlay_state.get('user_paused'):
                time.sleep(1.0)
                continue

            t_start = time.time()

            # 决定截哪个窗口
            actual_window = window
            window_for_screenshot = window  # 实际传给 screenshot() 的窗口名

            if auto_window:
                # 🔄 自动跟随前台窗口（每次截屏前重新检测）
                fg = get_foreground_window()
                if fg:
                    fg_title, _ = fg
                    # 🔒 隐私保护：黑名单窗口 → 跳过这一轮
                    if _is_window_blacklisted(fg_title):
                        if not fallback_announced:
                            ts_w = datetime.now().strftime('%H:%M:%S')
                            print(f"  [{ts_w}] 🔒 黑名单窗口『{fg_title[:30]}』，跳过截屏保护隐私")
                            fallback_announced = True
                        # 🫧 E: 浮窗显示暂停状态
                        overlay_state['paused'] = True
                        overlay_state['window'] = fg_title[:30].strip()
                        time.sleep(ocr_interval if ocr_enabled else interval)
                        continue
                    fallback_announced = False
                    overlay_state['paused'] = False
                    # P: 归一化窗口名（chrome 切 tab 不分裂）
                    actual_window = normalize_window_key(fg_title)
                    window_for_screenshot = fg_title
                else:
                    actual_window = None
                    window_for_screenshot = None
            elif window:
                hwnd = _find_window(window)
                if not hwnd:
                    actual_window = None
                    window_for_screenshot = None
                    if not fallback_announced:
                        ts_w = datetime.now().strftime('%H:%M:%S')
                        print(f"  [{ts_w}] ⚠️ 找不到窗口『{window}』(可能切了 tab/关了窗)，临时 fallback 全屏")
                        fallback_announced = True
                else:
                    if fallback_announced:
                        ts_w = datetime.now().strftime('%H:%M:%S')
                        print(f"  [{ts_w}] ✓ 窗口『{window}』回来了，恢复抓该窗口")
                    fallback_announced = False
                    # P: 归一化 (用 -w 时也走归一化，避免手填的标题被截 tab 变化)
                    actual_window = normalize_window_key(window)

            # 截屏（找不到窗口自动 fallback 全屏，不卡住）
            try:
                img = screenshot(window_for_screenshot, fallback_to_fullscreen=True)
                # 应用 crop 裁掉 browser UI 等敏感区域
                if crop_top > 0 or crop_bottom > 0:
                    img = crop_borders(img, top=crop_top, bottom=crop_bottom)
            except Exception as e:
                print(f"  ⚠️  截屏失败：{type(e).__name__}: {e}")
                time.sleep(ocr_interval if ocr_enabled else interval)
                continue

            # 每 30s 异步推一张高清图给 VPS（供 AI 主动调拿原图看细节）
            # T: 用户点"立即推快照" → 立刻推一张，不管 timer
            if t_start >= next_snap_push_time or overlay_state.get('force_snap'):
                push_snap_async(img)
                next_snap_push_time = t_start + SNAP_INTERVAL
                if overlay_state.get('force_snap'):
                    overlay_state['force_snap'] = False
                    print(f"  [{datetime.now().strftime('%H:%M:%S')}] 📸 用户触发快照")

            # ── V: 主循环只截屏 + 写共享状态，OCR/Caption 都是独立 thread ──
            cur_hash = perceptual_hash(img)

            # 写给 OCR worker（永远是最新一帧）
            if ocr_enabled:
                with ocr_state_lock:
                    ocr_state['pending_img'] = img
                    ocr_state['pending_hash'] = cur_hash
                    ocr_state['pending_window'] = actual_window
                    ocr_state['pending_ts'] = time.time()

            # 写给 caption thread (D: 多帧融合)
            cap_state['frame_history'].append(img)
            if len(cap_state['frame_history']) > 3:
                cap_state['frame_history'] = cap_state['frame_history'][-3:]
            cap_state['last_window'] = actual_window
            cap_state['last_hash'] = cur_hash

            # 🫧 E: 同步当前窗口名（即便没新内容）
            overlay_state['window'] = actual_window or '(全屏)'

            # 等下一轮 capture
            elapsed = time.time() - t_start
            wait = max(0.05, capture_interval - elapsed)
            time.sleep(wait)

        except KeyboardInterrupt:
            print(f"\n[gaze] 停止")
            cap_state['stop'] = True
            ocr_state['stop'] = True
            if audio_transcriber:
                audio_transcriber.stop()
            print(f"  caption push: {cap_success} 次")
            print(f"  OCR push:     {ocr_pushes} 次")
            print(f"  audio push:   {audio_pushes} 次")
            print(f"  log: {log_file}")
            break
        except Exception as e:
            print(f"  ⚠️ 未预期错误: {type(e).__name__}: {e}")
            time.sleep(ocr_interval if ocr_enabled else interval)


def main():
    parser = argparse.ArgumentParser(
        description="gaze - 给你的 AI 借一只眼睛",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--provider', '-p', default='glm', choices=PROVIDERS.keys(),
                        help='vision API provider (default: glm)')
    parser.add_argument('--interval', '-i', type=int, default=10,
                        help='caption 截屏间隔，秒 (default: 10)')
    parser.add_argument('--window', '-w', default=None,
                        help='窗口标题（模糊匹配，contains），不填 = 全屏')
    parser.add_argument('--style', '-s', default='danmu', choices=['danmu', 'detailed'],
                        help='caption 风格 (default: danmu)')
    parser.add_argument('--hash-threshold', type=int, default=4,
                        help='画面变化阈值 (default: 4)')
    parser.add_argument('--no-ocr', action='store_true',
                        help='禁用 OCR 快通道（只用 caption）')
    parser.add_argument('--ocr-interval', type=float, default=2.0,
                        help='OCR 截屏间隔，秒 (default: 2.0)')
    parser.add_argument('--crop-top', type=int, default=0,
                        help='截图顶部裁掉多少像素（隐藏浏览器地址栏/书签栏，建议 150）')
    parser.add_argument('--crop-bottom', type=int, default=0,
                        help='截图底部裁掉多少像素（隐藏 Windows 任务栏，建议 50）')
    parser.add_argument('--ocr-max-size', type=int, default=1920,
                        help='OCR 图片最大边长（默认 1920 不缩 1080p；小字幕场景别调低）')
    parser.add_argument('--auto-window', action='store_true',
                        help='🔄 自动跟随前台窗口（你切到哪个窗口 gaze 就截哪个，不用重启）')
    parser.add_argument('--audio', action='store_true',
                        help='🔊 启用音频字幕（抓系统声音 + Whisper 转字幕，首次启动会下载模型几分钟）')
    parser.add_argument('--audio-model', default='tiny', choices=['tiny', 'base', 'small'],
                        help='Whisper 模型 (default: tiny ~75MB 最快；base 142MB；small 466MB 更准)')
    parser.add_argument('--no-overlay', action='store_true',
                        help='禁用 🫧 实时浮窗（默认开，让你瞄一眼就知道 gaze 抓到啥）')
    parser.add_argument('--text-fast', action='store_true',
                        help='📖 文字游戏快读模式（capture 0.3s + 关 caption；玩 DDLC/galgame 1s 翻多页用）')
    parser.add_argument('--list-windows', action='store_true',
                        help='列出当前所有窗口（找窗口标题用）')
    parser.add_argument('--no-push', action='store_true',
                        help='不推到 VPS，只写本地 ~/.gaze/logs/*.jsonl（离线模式，无 VPS 时用）')
    parser.add_argument('--ssh-host',
                        help='VPS SSH host（覆盖 $GAZE_SSH_HOST 环境变量，默认 your-vps）')
    args = parser.parse_args()

    # --no-push / --ssh-host 翻起全局开关
    global _NO_PUSH, _SSH_HOST
    if args.no_push:
        _NO_PUSH = True
    if args.ssh_host:
        _SSH_HOST = args.ssh_host

    if args.list_windows:
        windows = list_windows()
        print(f"\n当前可见窗口 ({len(windows)} 个):\n")
        for title, hwnd in sorted(windows):
            print(f"  [{hwnd:>10}]  {title}")
        return

    run(args.provider, args.interval, args.window, args.style,
        args.hash_threshold,
        ocr_enabled=not args.no_ocr,
        ocr_interval=args.ocr_interval,
        crop_top=args.crop_top,
        crop_bottom=args.crop_bottom,
        ocr_max_size=args.ocr_max_size,
        auto_window=args.auto_window,
        audio_enabled=args.audio,
        audio_model=args.audio_model,
        overlay_enabled=not args.no_overlay,
        text_fast=args.text_fast)


if __name__ == '__main__':
    main()

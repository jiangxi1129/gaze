"""VPS 端：接收 stdin caption JSON，写入 memories.json 的 _realtime:* keys

字幕 anchor + sidecar 分层（F1）：
  - _realtime:window:<name>     窗口流，每条只保留 ≤24 字符锚点（AI 默认看这个）
  - _realtime:subtitles:<name>  完整字幕轨 sidecar（AI 要全文时拉这个）
  - 锚点截短逻辑灵感来自 idleprocesscc/film-matinee 的 pick_subtitle_anchor

写盘并发安全：
  fcntl.flock 锁 .memories.lock 文件（不是 memories.json 本身），跟同目录
  其他写 memories.json 的进程（你的 memory server / cron 脚本）互斥。
  防 lost update：进程 A 读 → 改 → 写时，进程 B 同时读 → 改 → 写覆盖 A。

调用方式：
    echo '{"caption": "...", "ts": "...", "source": "ocr"|"cap"|"audio", "window": "..."}' | \\
        ssh <你的VPS> 'python3 /path/to/push_caption.py'
"""
from __future__ import annotations

import json
import os
import sys
import time
import fcntl
from datetime import datetime
from pathlib import Path

# share 版：memories.json 路径从 env var 读取
STORE = Path(os.environ.get('MEMORIES_JSON', '/root/.mcp-memory/memories.json'))
LOCK_FILE = STORE.parent / '.memories.lock'   # ★ 跟同目录其他写 memories.json 的进程互锁

# Keys
KEY_TIMELINE = '_realtime:screen_caption'
KEY_WINDOW_PREFIX = '_realtime:window:'
KEY_SUBTITLES_PREFIX = '_realtime:subtitles:'  # F1: 完整字幕轨 sidecar
KEY_CURRENT = '_realtime:current_window'

# 容量
MAX_TIMELINE = 10            # 时间线锚点
MAX_PER_WINDOW = 30          # 每窗口锚点流
MAX_SUBTITLES = 200          # 每窗口字幕完整轨（多一点，长对话不丢）

# F1: 锚点截短
ANCHOR_MAX_CHARS = 24


def _make_anchor(text: str, max_chars: int = ANCHOR_MAX_CHARS) -> str:
    """F1: 把完整文本截短成 ≤24 字符锚点（视觉对齐用）

    保留前缀 [屏上文字] / [音频] 让 AI 识别 source 类型；只截内容部分。
    """
    if not text:
        return ''
    prefix = ''
    body = text
    for p in ('[屏上文字] ', '[音频] '):
        if text.startswith(p):
            prefix = p
            body = text[len(p):]
            break
    if len(body) <= max_chars:
        return prefix + body
    return prefix + body[:max_chars - 1] + '…'


def _parse_list(raw) -> list:
    """安全解析 list (str → JSON / list → 原样 / 其他 → 空)"""
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else []
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def _next_id(existing_entries: list) -> int:
    """生成新 entry 的 id (epoch ms)，避免跟现有最大 id 冲突"""
    now_ms = int(time.time() * 1000)
    max_existing = max(
        (e.get('id', 0) for e in existing_entries if isinstance(e, dict)),
        default=0,
    )
    return max(now_ms, max_existing + 1)


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print('ERR: empty stdin', file=sys.stderr)
        return 1

    try:
        new_entry = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f'ERR: invalid json: {e}', file=sys.stderr)
        return 1

    if 'caption' not in new_entry or not new_entry['caption']:
        print('ERR: missing caption field', file=sys.stderr)
        return 1

    if 'ts' not in new_entry:
        new_entry['ts'] = datetime.now().isoformat()

    full_caption = new_entry['caption']
    source = new_entry.get('source', 'cap')
    is_subtitle_source = source in ('ocr', 'audio')

    # F1: 准备两个版本
    anchor_caption = _make_anchor(full_caption)
    anchor_entry = dict(new_entry)
    anchor_entry['caption'] = anchor_caption
    # 完整版保留 full caption，加一个 _full 标记
    full_entry = dict(new_entry)
    full_entry['_full'] = True

    # window 名归一化
    window = (new_entry.get('window') or 'fullscreen').strip()
    window_safe = window.replace(':', '_').strip() or 'fullscreen'

    STORE.parent.mkdir(parents=True, exist_ok=True)
    if not STORE.exists():
        STORE.write_text('{}', encoding='utf-8')

    # ★ 跨进程写盘锁：跟同目录其他写 memories.json 的进程互斥
    with open(LOCK_FILE, 'w') as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            # 锁内读
            try:
                data = json.loads(STORE.read_text(encoding='utf-8'))
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}

            # 1. timeline (锚点)
            timeline = _parse_list(data.get(KEY_TIMELINE, ''))
            if 'id' not in anchor_entry:
                anchor_entry['id'] = _next_id(timeline)
            timeline.append(anchor_entry)
            timeline_trimmed = timeline[-MAX_TIMELINE:]
            data[KEY_TIMELINE] = json.dumps(timeline_trimmed, ensure_ascii=False)

            # 2. 窗口流 (锚点)
            win_key = f'{KEY_WINDOW_PREFIX}{window_safe}'
            win_list = _parse_list(data.get(win_key, ''))
            win_list.append(anchor_entry)
            win_trimmed = win_list[-MAX_PER_WINDOW:]
            data[win_key] = json.dumps(win_trimmed, ensure_ascii=False)

            # 3. F1: 字幕完整轨 sidecar (只 ocr/audio)
            sub_count = 0
            if is_subtitle_source:
                sub_key = f'{KEY_SUBTITLES_PREFIX}{window_safe}'
                sub_list = _parse_list(data.get(sub_key, ''))
                full_entry['id'] = anchor_entry['id']  # 共享 id 便于对齐
                sub_list.append(full_entry)
                sub_trimmed = sub_list[-MAX_SUBTITLES:]
                data[sub_key] = json.dumps(sub_trimmed, ensure_ascii=False)
                sub_count = len(sub_trimmed)

            # 4. current_window
            data[KEY_CURRENT] = window_safe

            # 原子写：tmp + rename
            tmp = STORE.with_suffix(STORE.suffix + '.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(STORE)

            timeline_count = len(timeline_trimmed)
            win_count = len(win_trimmed)
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    sub_msg = f' | sidecar "{KEY_SUBTITLES_PREFIX}{window_safe}" {sub_count}' if is_subtitle_source else ''
    print(f'OK | anchor "{anchor_caption}" ({len(full_caption)}ch full) | timeline {timeline_count} | window {win_count}{sub_msg}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

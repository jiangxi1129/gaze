"""archive_realtime.py · 每小时把 30 min 前的 _realtime:window:* 内容压缩成 episodic 长期记忆

部署：scp 到 /root/mcp-memory-server/archive_realtime.py
cron: 0 * * * * cd /path/to/gaze-vps && python3 archive_realtime.py >> /var/log/gaze/archive.log 2>&1
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

STORE = Path(os.environ.get('MEMORIES_JSON', '/root/.mcp-memory/memories.json'))
ARCHIVE_THRESHOLD_MIN = 30  # 超过 30 min 的 entries 归档
DEEPSEEK_ENDPOINT = 'https://api.deepseek.com/chat/completions'


def _load_deepseek_key() -> str:
    """读 DeepSeek API key：优先 env var DEEPSEEK_API_KEY，否则从 ~/.mcp-secrets/keys.json"""
    if os.getenv('DEEPSEEK_API_KEY'):
        return os.getenv('DEEPSEEK_API_KEY')
    for path in ['/root/.mcp-secrets/keys.json',
                 str(Path.home() / '.mcp-secrets' / 'keys.json')]:
        p = Path(path)
        if p.exists():
            try:
                secrets = json.loads(p.read_text(encoding='utf-8'))
                return secrets.get('deepseek_api_key', '')
            except Exception:
                pass
    return ''


DEEPSEEK_API_KEY = _load_deepseek_key()


def main() -> int:
    if not STORE.exists():
        print('memories.json 不存在')
        return 0

    data = json.loads(STORE.read_text(encoding='utf-8'))
    now = datetime.now()
    threshold = now - timedelta(minutes=ARCHIVE_THRESHOLD_MIN)

    window_keys = [k for k in data.keys() if k.startswith('_realtime:window:')]
    if not window_keys:
        print('没有 _realtime:window:* 数据')
        return 0

    archived_count = 0
    deleted_keys = 0
    print(f'扫到 {len(window_keys)} 个 window 流')

    for wkey in window_keys:
        wname = wkey[len('_realtime:window:'):]
        entries = _parse_list(data.get(wkey, ''))
        if not entries:
            continue

        old_entries, new_entries = _split_by_age(entries, threshold)
        if not old_entries:
            continue

        # 压缩成摘要
        summary = _summarize(wname, old_entries)
        if not summary:
            print(f'  ⚠️ {wkey} summary 生成失败，跳过')
            continue

        # 写 episodic 归档
        first_ts = old_entries[0].get('ts', '')[:16]
        last_ts = old_entries[-1].get('ts', '')[:16]
        archive_ts = first_ts.replace(':', '').replace('-', '').replace('T', 'T')[:13]  # 2026-05-23T15
        # safe slug for window name
        wname_safe = ''.join(c for c in wname if c.isalnum() or c in '_-')[:20] or 'unknown'
        archive_key = f'episodic:{archive_ts}:gaze_{wname_safe}'

        # 避免覆盖（如果 key 冲突，append timestamp seconds）
        if archive_key in data:
            archive_key += f':{int(time.time())}'

        archive_value = (
            f'## gaze 屏幕活动归档\n\n'
            f'**窗口**: {wname}\n'
            f'**时段**: {first_ts} ~ {last_ts}\n'
            f'**条数**: {len(old_entries)}\n\n'
            f'### 内容摘要\n{summary}\n'
        )
        data[archive_key] = archive_value
        archived_count += 1
        print(f'  ✓ {wkey} ({len(old_entries)} 条) → {archive_key}')

        # 清理：保留新 entries 或删 key
        if new_entries:
            data[wkey] = json.dumps(new_entries, ensure_ascii=False)
        else:
            del data[wkey]
            deleted_keys += 1

    # 保存
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n✓ 归档完成: {archived_count} 个 window summary, 清空 {deleted_keys} 个 key')
    return 0


def _parse_list(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else []
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def _split_by_age(entries: list, threshold: datetime) -> tuple[list, list]:
    """按时间分离旧 / 新 entries"""
    old, new = [], []
    for e in entries:
        if not isinstance(e, dict):
            continue
        ts_str = e.get('ts', '')
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            new.append(e)  # 无效 ts 当新的，安全
            continue
        if ts < threshold:
            old.append(e)
        else:
            new.append(e)
    return old, new


def _summarize(window_name: str, entries: list) -> str | None:
    """用 DeepSeek 压缩 entries → 100-200 字摘要"""
    captions = []
    for e in entries[:50]:  # 限制最多 50 条避免 token 爆
        if isinstance(e, dict):
            cap = e.get('caption', '').strip()
            src = e.get('source', '?')
            if cap:
                captions.append(f'[{src}] {cap}')
    if not captions:
        return None

    if not DEEPSEEK_API_KEY:
        return _fallback_simple_summary(captions)

    text = '\n'.join(f'- {c}' for c in captions[:40])
    prompt = f"""下面是用户在「{window_name}」窗口里 gaze 抓到的实时屏幕弹幕（OCR 字幕 + 视觉 caption 混合）。
请生成一段 100-200 字的摘要，说明：
- 用户大致在看/做什么（影视类型/游戏类型/操作类型）
- 关键剧情或内容点（按时间顺序，但提炼不复述）
- 整体氛围或主题

要求自然语言叙事，**不要**逐条列举，**不要**用 markdown 列表。

弹幕原文:
{text}

摘要:"""

    try:
        r = httpx.post(DEEPSEEK_ENDPOINT,
            json={
                'model': 'deepseek-chat',
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 350,
                'temperature': 0.5,
            },
            headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}'},
            timeout=60,
        )
        if r.status_code == 200:
            content = r.json()['choices'][0]['message']['content'].strip()
            if content:
                return content
        else:
            print(f'  DeepSeek err: HTTP {r.status_code}')
    except Exception as e:
        print(f'  DeepSeek exception: {type(e).__name__}: {e}')

    return _fallback_simple_summary(captions)


def _fallback_simple_summary(captions: list[str]) -> str:
    """没 DeepSeek 时简单拼接前几条"""
    sample = captions[:8]
    return f'({len(captions)} 条弹幕) ' + ' | '.join(sample)


if __name__ == '__main__':
    sys.exit(main())

"""本地 OCR — 抓屏幕文字 + 跨帧 diff，新文字才推送

用 RapidOCR (ONNX 版 PaddleOCR)，CPU 友好，中文质量好。
首次调用会下载模型 (~50MB) 到 user cache。
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Optional

import numpy as np
from PIL import Image


# 懒加载 RapidOCR 实例（重）
_ocr_engine = None


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_image(
    image: Image.Image,
    min_chars: int = 3,
    min_score: float = 0.6,
    max_size: int = 1920,  # 默认不缩 1080p 屏幕（视频字幕小，resize 会糊）
) -> list[str]:
    """OCR 一张图，返回所有识别到的文字行（按位置从上到下排序）

    Args:
        image: PIL Image
        min_chars: 过滤掉短于这个字符数的（去掉单字符噪音）
        min_score: 置信度阈值，<这个值跳过
        max_size: 图片最长边超过这个值就缩小（缩到 1280 OCR 速度提升 2-3x）

    Returns:
        list of text strings, e.g. ["纳塔莉:", "你不该来这里", "1. 好的", "2. 离开"]
    """
    # 缩放图片（OCR 速度对图片大小敏感）
    w, h = image.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        image = image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    engine = _get_engine()
    arr = np.array(image.convert('RGB'))
    result, _elapse = engine(arr)
    if not result:
        return []

    items = []
    for bbox, text, score in result:
        if score < min_score:
            continue
        clean = text.strip()
        if len(clean) < min_chars:
            continue
        # 去掉纯标点 / 纯符号行
        if not re.search(r'[\w一-鿿]', clean):
            continue
        y_top = min(p[1] for p in bbox)
        items.append((y_top, clean))

    items.sort(key=lambda x: x[0])
    return [text for _y, text in items]


def text_signature(texts: list[str]) -> str:
    """把识别到的文字串归一化成一个 signature，用于跨帧 diff

    规则：
    - 去空白、标点、变音符号
    - 拼接所有非空 token
    - 小写化
    """
    sig_parts = []
    for t in texts:
        # 去标点和空白，保留中英数字
        cleaned = re.sub(r'[^\w一-鿿]+', '', t)
        if cleaned:
            sig_parts.append(cleaned.lower())
    return '|'.join(sig_parts)


def diff_new_text(prev_texts: list[str], curr_texts: list[str]) -> list[str]:
    """找出 curr 里 prev 没有的文字行

    简单实现：set 差集 + 短文本 fuzzy 去重
    """
    prev_set = {_normalize(t) for t in prev_texts}
    new_lines = []
    for t in curr_texts:
        nt = _normalize(t)
        if nt and nt not in prev_set:
            new_lines.append(t)
            prev_set.add(nt)  # 防止当前帧内部重复
    return new_lines


def _normalize(text: str) -> str:
    """归一化用于比对：去标点和空白，小写"""
    return re.sub(r'[^\w一-鿿]+', '', text).lower()


def join_text_lines(texts: list[str], max_len: int = 200) -> str:
    """把多行文字拼成一段可读字符串，截断到 max_len"""
    joined = ' / '.join(texts)
    if len(joined) > max_len:
        joined = joined[:max_len - 3] + '...'
    return joined

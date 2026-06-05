"""Mock provider — 不需要 API key，用图片特征生成假 caption

用于：
- 没注册任何 vision API 时先验证 pipeline 跑通
- 调试时不烧 API 额度

工作原理：分析图片主色调 + 亮度 + 尺寸，随机抽一句假 caption。
"""
from __future__ import annotations

import random
from PIL import Image

from .base import CaptionProvider


_DANMU_TEMPLATES = [
    "画面静下来了，要发生什么",
    "BGM 一变心都跟着颤",
    "这一帧好美啊存图",
    "等等他刚才说什么",
    "感觉有内涵但说不出来",
    "你看主角的眼神在转",
    "这场景有点像我家的小时候",
    "好像有人要哭了",
    "这角色又开始装",
    "她的表情……糟糕",
    "突然加快了节奏",
    "导演这是想搞事啊",
    "这色调好戏",
    "光打过来的时候真好看",
    "氛围感拉满了",
    "镜头一切完全不一样了",
    "字幕跟我说了重要的事",
    "等等让我消化一下",
]


class MockCaptioner(CaptionProvider):
    name = "mock"

    def __init__(self, **kwargs):
        # 接受任意 kwargs，方便跟其他 provider 一致
        pass

    def caption(self, image: Image.Image, style: str = 'danmu',
                recent_ocr: list[str] | None = None) -> str:
        # 用图片像素的简单特征做"伪随机种子"，让同一张图给一致 caption
        w, h = image.size
        small = image.resize((4, 4)).convert('L')
        avg = sum(small.getdata()) / 16
        seed = int(avg * 1000 + w + h) % len(_DANMU_TEMPLATES)
        return _DANMU_TEMPLATES[seed]

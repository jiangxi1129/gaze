"""阿里 Qwen-VL-Plus provider（OpenAI 兼容模式）

注册：https://dashscope.aliyun.com → API-KEY 管理
价格（2026）：qwen-vl-plus ¥0.008/k tokens 输入 ¥0.008/k tokens 输出
免费额度：新用户有 100 万 tokens 试用

环境变量：
  QWEN_API_KEY
"""
from __future__ import annotations

import os

import httpx
from PIL import Image

from .base import CaptionProvider, PROMPTS


class QwenCaptioner(CaptionProvider):
    name = "qwen-vl-plus"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen-vl-plus",
        endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.getenv('QWEN_API_KEY')
        if not self.api_key:
            raise ValueError(
                "需要 QWEN_API_KEY — 在 dashscope.aliyun.com 注册拿 key，填进 .env"
            )
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout

    def caption(self, image: Image.Image, style: str = 'danmu',
                recent_ocr: list[str] | None = None) -> str:
        img_b64 = self._img_to_base64(image)
        prompt = PROMPTS.get(style, PROMPTS['danmu'])
        if recent_ocr:
            ocr_context = '\n'.join(f'  • {t}' for t in recent_ocr[-6:])
            prompt = f"【OCR 已抓字幕（你不用重复）】\n{ocr_context}\n\n---\n\n{prompt}"

        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
            "max_tokens": 100,
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()

        text = data['choices'][0]['message']['content']
        return self._clean_caption(text)

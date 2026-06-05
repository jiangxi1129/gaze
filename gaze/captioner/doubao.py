"""字节豆包 vision provider

注册：https://www.volcengine.com/product/ark → 创建接入点（推理 endpoint）
注意：豆包跟 GLM/Qwen 不同，需要先创建一个 endpoint，拿 endpoint_id 作为 model 名

环境变量：
  DOUBAO_API_KEY
  DOUBAO_ENDPOINT_ID  ← 比如 ep-20240101-xxxxx
"""
from __future__ import annotations

import os

import httpx
from PIL import Image

from .base import CaptionProvider, PROMPTS


class DoubaoCaptioner(CaptionProvider):
    name = "doubao-vision"

    def __init__(
        self,
        api_key: str | None = None,
        endpoint_id: str | None = None,
        endpoint: str = "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.getenv('DOUBAO_API_KEY')
        self.endpoint_id = endpoint_id or os.getenv('DOUBAO_ENDPOINT_ID')
        if not (self.api_key and self.endpoint_id):
            raise ValueError(
                "需要 DOUBAO_API_KEY + DOUBAO_ENDPOINT_ID — 在 volcengine.com/product/ark 注册"
            )
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
            "model": self.endpoint_id,  # 豆包用 endpoint_id 作 model 名
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

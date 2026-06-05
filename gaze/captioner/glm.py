"""智谱 GLM-4V-Flash provider

注册：https://bigmodel.cn → 控制台 → API Keys
API 文档：https://bigmodel.cn/dev/api/normal-model/glm-4v

环境变量：
  GLM_API_KEY
"""
from __future__ import annotations

import os

import httpx
from PIL import Image

from .base import CaptionProvider, PROMPTS


class GLMCaptioner(CaptionProvider):
    name = "glm-4v-flash"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "glm-4v-flash",  # 免费、便宜、最少 token
        # 备选: glm-4v-plus (更详细) / glm-4v (中等)
        # 注意: glm-5v-turbo 是 reasoning 模型，需要 max_tokens >= 500
        endpoint: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.getenv('GLM_API_KEY')
        if not self.api_key:
            raise ValueError(
                "需要 GLM_API_KEY — 在 bigmodel.cn 注册拿 key，填进 .env"
            )
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout

    def caption_multi_frame(self, frames: list, recent_ocr=None,
                            last_caption=None, style='danmu') -> str:
        """D: 多帧融合 caption — 一次给 3 张连续帧，描述变化/动作"""
        if not frames:
            raise ValueError("frames is empty")
        # 用最后 3 张
        frames = frames[-3:]
        images_payload = []
        for img in frames:
            b64 = self._img_to_base64(img)
            images_payload.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
            })

        prompt = PROMPTS.get(style, PROMPTS['danmu'])

        # OCR context
        if recent_ocr:
            ocr_ctx = '\n'.join(f'  • {t}' for t in recent_ocr[-6:])
            prompt = f"【OCR 已抓字幕，你不用重复】\n{ocr_ctx}\n\n---\n\n{prompt}"

        # B: 上一次 caption (避免重复)
        if last_caption:
            prompt = f"【上一次你说的】: \"{last_caption}\"\n\n---\n\n{prompt}\n\n⚠️ 这次描述跟上次的**变化/动作**，不要重复上次说过的场景"

        # 多帧指令
        n_frames = len(frames)
        prompt = (
            f"【这是 {n_frames} 张连续抓的帧（间隔几秒）】\n"
            "描述这段时间里的**动作变化/场景切换**（如'角色走到桌前坐下'）。\n\n---\n\n"
        ) + prompt

        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": images_payload + [{"type": "text", "text": prompt}],
            }],
            "max_tokens": 150,
            "temperature": 0.7,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()

        text = data['choices'][0]['message']['content']
        return self._clean_caption(text)

    def caption_with_context(self, image, frames=None, recent_ocr=None,
                              last_caption=None, style='danmu') -> str:
        """B: 单帧 + 上一次 caption context"""
        return self.caption(image, style=style, recent_ocr=recent_ocr,
                            last_caption=last_caption)

    def caption(self, image: Image.Image, style: str = 'danmu',
                recent_ocr: list[str] | None = None,
                last_caption: str | None = None) -> str:
        img_b64 = self._img_to_base64(image)
        prompt = PROMPTS.get(style, PROMPTS['danmu'])

        # 注入 OCR context（让 caption 知道字幕已经被抓过）
        if recent_ocr:
            ocr_context = '\n'.join(f'  • {t}' for t in recent_ocr[-6:])
            prompt = f"""【OCR 已经抓到这些文字（你不用重复）】:
{ocr_context}

---

{prompt}"""

        # B: 上一次 caption context
        if last_caption:
            prompt = f'【上一次你说】: "{last_caption}"\n\n---\n\n{prompt}\n\n⚠️ 这次描述跟上次的**变化**，不重复上次说过的'

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

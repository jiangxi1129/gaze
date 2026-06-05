"""gaze caption providers — 各家 vision 模型的统一接口"""
from .base import CaptionProvider, PROMPTS
from .glm import GLMCaptioner
from .qwen import QwenCaptioner
from .doubao import DoubaoCaptioner
from .mock import MockCaptioner

__all__ = ['CaptionProvider', 'PROMPTS', 'GLMCaptioner', 'QwenCaptioner', 'DoubaoCaptioner', 'MockCaptioner']

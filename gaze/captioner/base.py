"""Caption Provider 抽象基类 + prompt 模板"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from io import BytesIO

from PIL import Image


# ─── Caption 风格 prompts ───
PROMPTS = {
    'danmu': """你是给视障朋友做实时陪聊的解说员。把画面翻译成文字。

【重要】另一个 OCR 系统已经在抓屏上文字（对话框/字幕/UI）。
你的任务是描述 **OCR 抓不到的视觉信息**：
- 场景/环境（"教室"/"图书馆"/"街头"）
- 角色位置/动作（"坐在桌旁"/"转身离开"/"举手"）
- 表情/姿态（用中性描述：低头/侧脸/双手交叠）
- 关键视觉道具（"桌上有蜡烛"/"墙上挂画"）

【不要做的】
🚫 不要重复 OCR 已经抓到的字幕原文（除非你看到 OCR 漏的字）
🚫 严禁评论身材、外貌、性吸引力（"美女""小姐姐""身材"等）
🚫 用发色/服装/位置中性描述人物，不用"她""他"
🚫 不要主观解读情绪（"气氛阴森"等）
🚫 不用"图片显示""画面中"开头

【输出格式】
20-50 字，描述场景+人物动作。如果 OCR 漏了重要文字，可补一句"屏幕额外显示：X"。

【示例】
✅ "粉色短发角色站在教室中央，双手交叠胸前，背后是黑板和窗户"
✅ "紫发角色坐在图书馆木桌旁，低头看纸条，桌上有蜡烛"
✅ "主角房间，没人物在场，桌上笔记本电脑屏幕亮着"
✅ "棕发角色转身离开，画面虚化处理"
✅ "教室外走廊空无一人，灯光昏暗"

❌ 反面：
- "粉色头发的小姐姐好可爱"  ← 物化
- "图片显示..."             ← 书面化
- "对话框写：'你最喜欢哪首诗'"  ← 重复 OCR 已抓内容

输出一句视觉描述：""",

    'video': """你是给视障朋友做实时陪聊的解说员。这是视频/短剧/电影/直播画面。

【最重要的任务：字幕】
**屏幕上任何形式的对白字幕/台词字幕，必须逐字引用**——这是观众最关心的信息，缺了就等于剧情看不到。

【字幕在哪里】
- 通常在画面**底部**（白色字+黑色描边，或纯白带阴影）
- 也可能在顶部 PIP 区、画面中间、对话框里
- **字幕字往往不是画面里最大的字**：标题/角色名/水印才是大字，但那些是 UI 不是对白
- 看不到清晰字幕时才写"无字幕"

【输出格式（30-60 字）】
有字幕（优先这种）：
  `字幕"<原文>"。画面：<谁在做什么>`
  例："字幕"他要尊称三代的人为师兄"。画面：左右两张男生肖像对照"

无字幕（动作场景）：
  `<谁/什么在做什么。场景>`
  例："香肠形象主角抱住草莓头女角色，街头日景"

【严禁】
🚫 把视频里的**标题/章节名/角色名**当对白引用（那是 UI 不是字幕）
🚫 编台词 —— 字幕模糊或看不全就写"字幕模糊"
🚫 评论身材/外貌（"美女""小姐姐""可爱"等）
🚫 主观解读剧情/情绪（"气氛阴森""她很伤心"）
🚫 "图片显示""画面中"开头

输出：""",

    'detailed': """你是无障碍电影的解说员。
请详细描述当前画面：场景、人物动作、表情、关键道具。一句话内（50-80 字）。
不要解读情感、不要剧透剧情。只描述当前能看到的。
只输出描述本身，不要前缀后缀。""",
}


class CaptionProvider(ABC):
    """所有 vision 模型的统一接口"""

    name: str = "base"

    @abstractmethod
    def caption(self, image: Image.Image, style: str = 'danmu',
                recent_ocr: list[str] | None = None) -> str:
        """对图片生成 caption

        Args:
            image: PIL Image
            style: 'danmu' (短弹幕) 或 'detailed' (详细解说)
            recent_ocr: 最近的 OCR 文本列表（让 caption 知道字幕已被 OCR 抓过，避免重复描述）

        Returns:
            caption 文字
        """
        ...

    def _img_to_base64(self, image: Image.Image, max_size: int = 1024) -> str:
        """图片等比压到 max_size + 转 base64 (JPEG q=85)"""
        w, h = image.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            image = image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        buf = BytesIO()
        image.convert('RGB').save(buf, format='JPEG', quality=85)
        return base64.b64encode(buf.getvalue()).decode('ascii')

    def _clean_caption(self, text: str) -> str:
        """去掉模型可能加的引号/句号/换行/前缀"""
        text = text.strip()
        # 常见前缀
        for prefix in ('描述：', '弹幕：', '解说：', '画面：', '场景：'):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        # 去引号
        for q in ('"', "'", '"', '"', '「', '」', "'", "'"):
            text = text.strip(q)
        # 去句末标点（保留逗号问号感叹号）
        text = text.rstrip('。.')
        return text.strip()

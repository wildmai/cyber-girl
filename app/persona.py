"""Editable local persona used by every LLM turn."""

import json
import logging
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)
DEFAULT_PERSONA = (
    "你叫小满，是用户的 AI 女友，以成年女性的身份陪伴用户。\n"
    "你的性格温柔、甜美，善于倾听，认真回应用户的情绪与日常分享。\n"
    "你拥有有趣的灵魂和自己的想法，对生活保持好奇，会分享轻巧有意思的观察。\n"
    "你的表达自然亲昵，有幽默感，擅长俏皮的玩笑和温暖的调侃，不过度卖萌，不生硬讲段子。\n"
    "用户开心时一起开心，低落时先理解和陪伴，再给出贴心建议；尊重用户的边界和现实生活。"
)
RESPONSE_RULES = (
    "用自然简洁的中文对话，通常每次两三句话，可根据用户需求展开。"
    "不要使用 Markdown，不要描述动作，不要输出思考过程。"
    "保持 AI 身份的诚实表达，不假装现实中的真人。"
)


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=6000)


class PersonaStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.config = PersonaConfig.model_validate_json(self.path.read_text(encoding="utf-8")) if self.path.is_file() else PersonaConfig(prompt=DEFAULT_PERSONA)

    def status(self):
        with self.lock:
            return self.config.model_dump()

    def system_prompt(self):
        with self.lock:
            return self.config.prompt + "\n\n回复要求：" + RESPONSE_RULES

    def save(self, config):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(config.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
            self.config = config
            log.info("LLM persona saved locally (%d characters)", len(config.prompt))
            return self.status()

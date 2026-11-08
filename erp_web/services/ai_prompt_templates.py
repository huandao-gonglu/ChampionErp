"""随应用发布的系统提示词；不读取用户目录或可配置路径。"""

from pathlib import Path
import json
from typing import Any

SYSTEM_PROMPT_DIR = Path(__file__).resolve().parents[1] / "assets" / "prompts"
SYSTEM_PROMPT_FILES = {
    "global.chat": "global_chat.json",
    "copy.generate": "copy_generate.json",
    "image.translate": "image_translate.json",
    "image.plan": "image_set_plan.json",
    "category.product_match": "category_product_match.json",
    "text.translate": "text_translate.json",
}


def load_ai_use_case_prompt_pair(use_case_id: str) -> dict[str, str]:
    """只加载发布包中的明确文件；缺失或无效时拒绝运行，不回退旧用户配置。"""
    name = SYSTEM_PROMPT_FILES.get(use_case_id)
    if name is None:
        raise ValueError(f"AI 功能没有系统提示词：{use_case_id}")
    path = SYSTEM_PROMPT_DIR / name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        system, user = raw["system"], raw["user"]
        if isinstance(system, list) and all(isinstance(item, str) for item in system):
            system = "".join(system)
        if (not isinstance(system, str) or not isinstance(user, str) or not user.strip()
                or (not system.strip() and use_case_id != "image.translate")):
            raise ValueError("系统提示词内容无效")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"系统提示词无法加载：{name}，请检查应用安装文件。") from exc
    return {"system": system.rstrip(), "user": user.rstrip()}


def render_prompt_template(template: str, context: dict[str, Any]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{$" + key + "}", str(value))
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


__all__ = ["SYSTEM_PROMPT_FILES", "load_ai_use_case_prompt_pair", "render_prompt_template"]

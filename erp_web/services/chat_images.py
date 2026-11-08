"""全局对话的图片准入与原生消息中的附件引用。"""
from __future__ import annotations

import base64
import binascii
import hashlib

from pydantic_ai.messages import BinaryContent, UserPromptPart

from erp_web.schemas.image_hosting import ImageHostingError
from . import ai_model_config
from .image_content import image_content

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_CHAT_IMAGES = 4
MAX_CHAT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CHAT_TOTAL_BYTES = 20 * 1024 * 1024


def chat_image_reason(config) -> str:
    try:
        model = ai_model_config.resolve_ai_model(config, "global.chat")
    except RuntimeError:
        model = {}
    if ai_model_config.model_has_capabilities(model, [ai_model_config.CAP_IMAGE_UNDERSTANDING]):
        return ""
    return "当前全局 AI 模型未启用图片理解，请在模型设置中测试并绑定支持识图的 API 模型。"


def validate_image_attachment(part) -> int:
    prefix = f"data:{part.media_type};base64,"
    if part.provider_metadata or not part.url.startswith(prefix):
        raise ValueError("请上传本地图片或直接粘贴图片。")
    encoded = part.url[len(prefix):]
    if len(encoded) > ((MAX_CHAT_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("单张图片不能超过 8 MiB。")
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_CHAT_IMAGE_BYTES:
            raise ValueError("单张图片不能超过 8 MiB。")
        _, _, actual_type = image_content(data)
    except (binascii.Error, ImageHostingError) as exc:
        raise ValueError("图片损坏或格式不支持，请使用 JPEG、PNG 或 WebP 图片。") from exc
    if actual_type != part.media_type:
        raise ValueError("图片内容与声明的格式不一致。")
    return len(data)


def identify_uploads(messages) -> None:
    # 模型请求可以合并相邻消息；把来源绑定到原生图片，避免依赖合并后的消息 metadata。
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                for index, item in enumerate(part.content):
                    if isinstance(item, BinaryContent) and item.is_image:
                        digest = hashlib.sha256(item.data).hexdigest()
                        part.content[index] = BinaryContent.narrow_type(BinaryContent(
                            data=item.data, media_type=item.media_type, identifier=f"chat_{digest}",
                        ))


def user_images(message) -> list[BinaryContent]:
    if not (message.metadata or {}).get("user_message_id"):
        return []
    return [item for part in message.parts if isinstance(part, UserPromptPart)
            and isinstance(part.content, (list, tuple)) for item in part.content
            if isinstance(item, BinaryContent) and item.is_image]


def prompt_summary(messages) -> str:
    lines = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                items = [part.content] if isinstance(part.content, str) else part.content
                lines.extend(item if isinstance(item, str) else "［图片］" for item in items)
    return "\n".join(lines)

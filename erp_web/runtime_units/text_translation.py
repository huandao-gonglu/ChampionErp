from __future__ import annotations

from typing import Any

from .ai_use_case import run_ai_use_case


MAX_TRANSLATION_ITEMS = 200
MAX_TRANSLATION_KEY_LENGTH = 200
MAX_TRANSLATION_TEXT_LENGTH = 10_000
MAX_TRANSLATION_TOTAL_CHARACTERS = 50_000


class TranslationRequestError(ValueError):
    """调用方提供的翻译请求不满足通用文本契约。"""


class TranslationResponseError(RuntimeError):
    """模型返回的数据不满足扁平键值响应契约。"""


def _normalize_translation_content(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise TranslationRequestError("翻译内容必须是非空键值对象。")
    if len(value) > MAX_TRANSLATION_ITEMS:
        raise TranslationRequestError(f"单次最多翻译 {MAX_TRANSLATION_ITEMS} 段文本。")
    normalized: dict[str, str] = {}
    total_characters = 0
    for raw_key, raw_text in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise TranslationRequestError("翻译内容包含空 key。")
        key = raw_key
        if len(key) > MAX_TRANSLATION_KEY_LENGTH:
            raise TranslationRequestError("翻译内容 key 过长。")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise TranslationRequestError(f"翻译内容 {key} 必须是非空字符串。")
        text = raw_text.strip()
        if len(text) > MAX_TRANSLATION_TEXT_LENGTH:
            raise TranslationRequestError(f"翻译内容 {key} 超过长度限制。")
        total_characters += len(text)
        if total_characters > MAX_TRANSLATION_TOTAL_CHARACTERS:
            raise TranslationRequestError("单次翻译文本总长度超过限制。")
        normalized[key] = text
    return normalized


def _normalize_translation_result(
    value: Any,
    expected_keys: tuple[str, ...],
) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TranslationResponseError("翻译模型必须返回 JSON 键值对象。")
    if set(value) != set(expected_keys):
        raise TranslationResponseError("翻译模型返回的 key 与请求不一致。")
    translations: dict[str, str] = {}
    for key in expected_keys:
        translated = value.get(key)
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationResponseError(f"翻译模型返回的 {key} 不是非空字符串。")
        translations[key] = translated.strip()
    return translations


def translate_texts(
    target_language: str,
    content: dict[str, Any],
) -> dict[str, str]:
    language = str(target_language or "").strip()
    if not language:
        raise TranslationRequestError("缺少目标翻译语言。")
    normalized = _normalize_translation_content(content)
    expected_keys = tuple(normalized)
    return run_ai_use_case(
        "text.translate",
        {
            "target_language": language,
            "content": normalized,
        },
        lambda value: _normalize_translation_result(value, expected_keys),
        temperature=0.1,
    )


__all__ = ["TranslationRequestError", "TranslationResponseError", "translate_texts"]

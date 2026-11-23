from __future__ import annotations

"""统一文本翻译入口（含 L2 服务端持久化 SQLite 词典与请求内去重）。

架构契约：
- ``text.translate`` 是全系统唯一的文本翻译 AI 用例；
- 常规请求（无 ``preserve_terms`` 且未 ``bypass_cache``）先查服务端持久化词典
  ``translation_glossary``，仅对未命中词条调用 ``run_ai_use_case("text.translate", ...)``，
  译完后按 ``(domain, target_language, sha256(source_text))`` 永久写回；
- 同一请求内重复出现的源文本仅发送一条给模型并扇出回填；
- 本模块对 ``run_ai_use_case`` 仅保留唯一一处调用。
"""

import logging
from typing import Any

from .ai_use_case import run_ai_use_case

logger = logging.getLogger(__name__)

MAX_TRANSLATION_ITEMS = 200
MAX_TRANSLATION_KEY_LENGTH = 200
MAX_TRANSLATION_TEXT_LENGTH = 10_000
MAX_TRANSLATION_TOTAL_CHARACTERS = 50_000
MAX_TRANSLATION_PRESERVE_TERMS = 100
MAX_TRANSLATION_PRESERVE_TERM_LENGTH = 500
_AI_BATCH_SIZE = 40


class TranslationRequestError(ValueError):
    """调用方提供的翻译请求不满足通用文本契约。"""


class TranslationResponseError(RuntimeError):
    """模型返回的数据不满足扁平键值响应契约。"""


def _catalog_store():
    from erp_web.context import get_context

    return get_context().category_catalog_store


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


def _normalize_preserve_terms(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TranslationRequestError("翻译保留词必须是字符串数组。")
    if len(value) > MAX_TRANSLATION_PRESERVE_TERMS:
        raise TranslationRequestError(
            f"单次最多保留 {MAX_TRANSLATION_PRESERVE_TERMS} 个术语。"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise TranslationRequestError("翻译保留词包含空值。")
        term = item.strip()
        if len(term) > MAX_TRANSLATION_PRESERVE_TERM_LENGTH:
            raise TranslationRequestError("翻译保留词过长。")
        if term not in seen:
            seen.add(term)
            normalized.append(term)
    # 长词优先，避免型号是另一个保留词子串时被提前替换。
    return tuple(sorted(normalized, key=lambda item: (-len(item), item)))


def _protect_preserve_terms(
    content: dict[str, str],
    preserve_terms: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    protected = dict(content)
    tokens: dict[str, str] = {}
    for index, term in enumerate(preserve_terms):
        token = f"__ERP_PRESERVE_{index}__"
        changed = False
        for key, text in protected.items():
            if term in text:
                protected[key] = text.replace(term, token)
                changed = True
        if changed:
            tokens[token] = term
    return protected, tokens


def _restore_preserve_terms(
    translations: dict[str, str],
    tokens: dict[str, str],
) -> dict[str, str]:
    restored = dict(translations)
    for token, term in tokens.items():
        for key, text in restored.items():
            if token not in text:
                raise TranslationResponseError(
                    f"翻译模型未保留受保护术语占位符：{token}。"
                )
            restored[key] = text.replace(token, term)
    return restored


def _run_model_translation(
    language: str,
    batch_content: dict[str, str],
    *,
    timeout_seconds: int | None = None,
) -> dict[str, str]:
    """模块内唯一调用 ``run_ai_use_case("text.translate", ...)`` 的位置。"""

    expected_keys = tuple(batch_content)
    return run_ai_use_case(
        "text.translate",
        {
            "target_language": language,
            "content": batch_content,
        },
        lambda value: _normalize_translation_result(value, expected_keys),
        temperature=0.1,
        **({"timeout_seconds": timeout_seconds} if timeout_seconds is not None else {}),
    )


def translate_texts_with_meta(
    target_language: str,
    content: dict[str, Any],
    *,
    preserve_terms: tuple[str, ...] | list[str] = (),
    timeout_seconds: int | None = None,
    domain: str = "general",
    cache_only: bool = False,
    bypass_cache: bool = False,
) -> dict[str, Any]:
    """执行 L2 词典查询 + L3 增量补翻并永久入库，返回译文及命中统计。"""

    language = str(target_language or "").strip()
    if not language:
        raise TranslationRequestError("缺少目标翻译语言。")
    normalized = _normalize_translation_content(content)
    normalized_terms = _normalize_preserve_terms(preserve_terms)
    normalized_domain = str(domain or "general").strip().lower() or "general"

    # 含保留词时必须逐项做占位符保护与还原，不混入通用词典。
    if normalized_terms:
        if cache_only:
            return {
                "translations": {},
                "source": "cache",
                "cache_hits": 0,
                "ai_translated": 0,
                "missing_keys": list(normalized),
            }
        protected_content, protected_tokens = _protect_preserve_terms(
            normalized,
            normalized_terms,
        )
        translated = _run_model_translation(
            language,
            protected_content,
            timeout_seconds=timeout_seconds,
        )
        restored = _restore_preserve_terms(translated, protected_tokens)
        return {
            "translations": restored,
            "source": "ai",
            "cache_hits": 0,
            "ai_translated": len(restored),
            "missing_keys": [],
        }

    # 请求内按源文本去重，避免重复词条占用上下文和 Token。
    rep_key_by_text: dict[str, str] = {}
    keys_by_text: dict[str, list[str]] = {}
    for key, text in normalized.items():
        if text not in rep_key_by_text:
            rep_key_by_text[text] = key
            keys_by_text[text] = []
        keys_by_text[text].append(key)

    unique_texts = list(rep_key_by_text)
    cached_by_text: dict[str, str] = {}
    use_cache = not bypass_cache
    if use_cache:
        try:
            cached_by_text = _catalog_store().get_glossary(
                normalized_domain,
                language,
                unique_texts,
                fallback_general=True,
            )
        except Exception:
            logger.debug("读取服务端翻译词典失败，回退直连模型翻译", exc_info=True)
            cached_by_text = {}

    missing_texts = [text for text in unique_texts if text not in cached_by_text]
    ai_by_text: dict[str, str] = {}

    if missing_texts and not cache_only:
        for start in range(0, len(missing_texts), _AI_BATCH_SIZE):
            chunk_texts = missing_texts[start : start + _AI_BATCH_SIZE]
            batch_payload = {rep_key_by_text[text]: text for text in chunk_texts}
            batch_result = _run_model_translation(
                language,
                batch_payload,
                timeout_seconds=timeout_seconds,
            )
            for text in chunk_texts:
                rep_key = rep_key_by_text[text]
                ai_by_text[text] = batch_result[rep_key]

        if ai_by_text and use_cache:
            try:
                _catalog_store().put_glossary(
                    normalized_domain,
                    language,
                    ai_by_text,
                )
            except Exception:
                logger.warning("写入服务端翻译词典失败", exc_info=True)

    resolved_by_text = {**cached_by_text, **ai_by_text}
    translations: dict[str, str] = {}
    missing_keys: list[str] = []
    cache_hits = 0
    ai_translated = 0
    for key, text in normalized.items():
        translated_value = resolved_by_text.get(text)
        if translated_value is not None:
            translations[key] = translated_value
            if text in cached_by_text:
                cache_hits += 1
            else:
                ai_translated += 1
        else:
            missing_keys.append(key)

    if cache_hits and ai_translated:
        source = "mixed"
    elif cache_hits and not ai_translated:
        source = "cache"
    else:
        source = "ai"

    return {
        "translations": translations,
        "source": source,
        "cache_hits": cache_hits,
        "ai_translated": ai_translated,
        "missing_keys": missing_keys,
    }


def translate_texts(
    target_language: str,
    content: dict[str, Any],
    *,
    preserve_terms: tuple[str, ...] | list[str] = (),
    timeout_seconds: int | None = None,
    domain: str = "general",
    cache_only: bool = False,
    bypass_cache: bool = False,
) -> dict[str, str]:
    result = translate_texts_with_meta(
        target_language,
        content,
        preserve_terms=preserve_terms,
        timeout_seconds=timeout_seconds,
        domain=domain,
        cache_only=cache_only,
        bypass_cache=bypass_cache,
    )
    return dict(result["translations"])


__all__ = [
    "TranslationRequestError",
    "TranslationResponseError",
    "translate_texts",
    "translate_texts_with_meta",
]

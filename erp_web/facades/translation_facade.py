from __future__ import annotations

import logging
from typing import Any

from erp_web.context import get_context
from erp_web.runtime_units.text_translation import (
    TranslationRequestError,
    translate_texts,
    translate_texts_with_meta,
)
from erp_web.services.ai_run_cancellation import check_cancellation
from erp_web.services.operation_availability import require_model_platform

logger = logging.getLogger(__name__)
_RUNTIME_TRANSLATE_TEXTS = translate_texts

Payload = dict[str, Any]
ResponseWithStatus = tuple[Payload, int]
_BATCH_SIZE = 100


def text_translate_payload(body: Payload) -> ResponseWithStatus:
    language = str(body.get("target_language") or "").strip()
    domain = str(body.get("domain") or body.get("platform") or "general").strip().lower() or "general"
    cache_only = bool(body.get("cache_only"))
    bypass_cache = bool(body.get("bypass_cache"))
    silent = bool(body.get("silent"))
    entries = list(body.get("content", {}).items()) if isinstance(body.get("content"), dict) else []

    translations: dict[str, str] = {}
    total_cache_hits = 0
    total_ai_translated = 0
    degraded = False
    model_checked = False

    try:
        for index in range(0, max(len(entries), 1), _BATCH_SIZE):
            check_cancellation()
            batch = dict(entries[index : index + _BATCH_SIZE])

            if translate_texts is not _RUNTIME_TRANSLATE_TEXTS:
                if not model_checked:
                    require_model_platform(
                        get_context().config.load_app_config(),
                        "text.translate",
                    )
                    model_checked = True
                translated_batch = translate_texts(language, batch)
                translations.update(translated_batch)
                total_ai_translated += len(translated_batch)
                continue

            # 1) 非 bypass_cache 时优先做 0ms 词典命中查询；全命中时无需校验 AI 模型。
            missing_batch = batch
            if not bypass_cache:
                cached_meta = translate_texts_with_meta(
                    language,
                    batch,
                    domain=domain,
                    cache_only=True,
                )
                translations.update(cached_meta["translations"])
                total_cache_hits += int(cached_meta["cache_hits"])
                missing_keys = set(cached_meta["missing_keys"])
                missing_batch = {k: v for k, v in batch.items() if k in missing_keys}

            if not missing_batch or cache_only:
                continue

            # 2) 存在词典未命中项：校验模型可用性；静默 L3 补翻失败时优雅降级返回已命中项。
            try:
                if not model_checked:
                    require_model_platform(
                        get_context().config.load_app_config(),
                        "text.translate",
                    )
                    model_checked = True
                ai_meta = translate_texts_with_meta(
                    language,
                    missing_batch,
                    domain=domain,
                    bypass_cache=bypass_cache,
                )
                translations.update(ai_meta["translations"])
                total_cache_hits += int(ai_meta["cache_hits"])
                total_ai_translated += int(ai_meta["ai_translated"])
            except TranslationRequestError:
                raise
            except Exception as exc:
                if not silent:
                    raise
                logger.info(
                    "静默本地化补翻已降级返回原文（domain=%s, missing=%d）：%s",
                    domain,
                    len(missing_batch),
                    exc,
                )
                degraded = True
                break
    except TranslationRequestError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "INVALID_TRANSLATION_REQUEST",
        }, 400

    if total_cache_hits and total_ai_translated:
        source = "mixed"
    elif total_cache_hits and not total_ai_translated:
        source = "cache"
    else:
        source = "ai"

    return {
        "ok": True,
        "translations": translations,
        "source": source,
        "cache_hits": total_cache_hits,
        "ai_translated": total_ai_translated,
        **({"degraded": True} if degraded else {}),
    }, 200


__all__ = ["text_translate_payload", "translate_texts"]

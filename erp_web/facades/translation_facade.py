from __future__ import annotations

from typing import Any

from erp_web.runtime_units.text_translation import TranslationRequestError, translate_texts
from erp_web.context import get_context
from erp_web.services.operation_availability import require_model_platform
from erp_web.services.ai_run_cancellation import check_cancellation


Payload = dict[str, Any]
ResponseWithStatus = tuple[Payload, int]
_BATCH_SIZE = 100


def text_translate_payload(body: Payload) -> ResponseWithStatus:
    require_model_platform(get_context().config.load_app_config(), "text.translate")
    language = str(body.get("target_language") or "").strip()
    entries = list(body.get("content", {}).items()) if isinstance(body.get("content"), dict) else []
    translations: dict[str, str] = {}
    try:
        # 页面的一次翻译可能包含多个服务商批次，共用原有单批校验。
        for index in range(0, max(len(entries), 1), _BATCH_SIZE):
            check_cancellation()
            translations.update(translate_texts(language, dict(entries[index:index + _BATCH_SIZE])))
    except TranslationRequestError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "INVALID_TRANSLATION_REQUEST",
        }, 400
    return {
        "ok": True,
        "translations": translations,
    }, 200


__all__ = ["text_translate_payload"]

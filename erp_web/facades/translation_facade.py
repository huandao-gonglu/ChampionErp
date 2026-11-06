from __future__ import annotations

from typing import Any

from erp_web.runtime_units.text_translation import TranslationRequestError, translate_texts
from erp_web.context import get_context
from erp_web.services.operation_availability import require_model_platform


Payload = dict[str, Any]
ResponseWithStatus = tuple[Payload, int]


def text_translate_payload(body: Payload) -> ResponseWithStatus:
    require_model_platform(get_context().config.load_app_config(), "text.translate")
    try:
        translations = translate_texts(
            str(body.get("target_language") or "").strip(),
            body.get("content") if isinstance(body.get("content"), dict) else {},
        )
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

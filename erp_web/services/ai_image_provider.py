"""OpenAI-compatible 图片 Provider。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import ai_model_config, image_service
from .ai_provider_contracts import (
    CAPABILITY_IMAGE_EDIT,
    CAPABILITY_IMAGE_GENERATE,
    AiImageProvider,
    AiImageRequest,
)


IMAGE_AI_TIMEOUT_SECONDS = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT_SECONDS", "180"))


def _response_items(response: Any) -> list[Any]:
    data = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    return data if isinstance(data, list) else []


def _item_value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _provider_results_from_response(
    response: Any,
    provider_name: str,
    mode: str,
    source_id: str = "",
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in _response_items(response):
        b64_json = str(_item_value(item, "b64_json") or "").strip()
        data_url = str(_item_value(item, "data_url") or _item_value(item, "dataUrl") or "").strip()
        url = str(_item_value(item, "url") or "").strip()
        if not any((b64_json, data_url, url)):
            continue
        result: dict[str, Any] = {
            "provider": provider_name,
            "mode": mode,
            "source_id": source_id,
            "suffix": ".png",
        }
        if b64_json:
            result["b64_json"] = b64_json
        if data_url:
            result["data_url"] = data_url
        if url:
            result["url"] = url
        results.append(result)
    return results


def _local_source_path(app_dir: Path | str, item: dict[str, Any]) -> Path | None:
    for key in ("path", "local_path"):
        candidate = image_service.resolve_local_path(str(item.get(key) or ""), app_dir)
        if candidate and candidate.exists() and candidate.is_file():
            return candidate
    return None


def _call_image_method(method: Any, payload: dict[str, Any]) -> Any:
    try:
        return method(**payload)
    except TypeError:
        if "quality" not in payload:
            raise
        fallback = dict(payload)
        fallback.pop("quality", None)
        return method(**fallback)


def _result_trace(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    traced: list[dict[str, Any]] = []
    for result in results:
        traced.append(
            {
                "provider": result.get("provider"),
                "mode": result.get("mode"),
                "source_id": result.get("source_id"),
                "url": result.get("url"),
                "has_base64": bool(result.get("b64_json") or result.get("data_url")),
            }
        )
    return traced


class OpenAIImageProvider(AiImageProvider):
    provider_id = "openai_image"

    def supports(self, model: dict[str, Any], capability: str) -> bool:
        return (
            capability in {CAPABILITY_IMAGE_GENERATE, CAPABILITY_IMAGE_EDIT}
            and ai_model_config.model_connection_type(model) == ai_model_config.CONNECTION_TYPE_API
        )

    @staticmethod
    def _client(model: dict[str, Any], timeout_seconds: int | None = None) -> Any:
        api_key = ai_model_config.model_api_key(model)
        if not api_key:
            raise RuntimeError("当前图片模型未配置 API Key。")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is not installed. Run: pip install openai") from exc
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": int(timeout_seconds or model.get("timeout_seconds") or IMAGE_AI_TIMEOUT_SECONDS),
            "default_headers": {"User-Agent": ai_model_config.AI_HTTP_USER_AGENT},
        }
        base_url = ai_model_config.model_base_url(model).rstrip("/")
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    @staticmethod
    def _provider_name(model: dict[str, Any]) -> str:
        return str(model.get("provider") or model.get("name") or "OpenAI-Compatible").strip() or "OpenAI-Compatible"

    def generate_images(self, request: AiImageRequest) -> list[dict[str, Any]]:
        client = self._client(request.model, request.timeout_seconds)
        model_name = ai_model_config.model_name(request.model) or "gpt-image-1"
        payload = {
            "model": model_name,
            "prompt": request.prompt,
            "n": request.count,
            "size": request.size,
            "quality": request.quality,
        }
        if request.conversation:
            request.conversation.emit_custom(
                "provider.request",
                {
                    "operation": "images.generate",
                    "provider_payload": payload,
                },
            )
        response = _call_image_method(client.images.generate, payload)
        results = _provider_results_from_response(
            response,
            self._provider_name(request.model),
            request.mode or "generate",
        )
        if request.conversation:
            request.conversation.emit_custom("provider.response", {"images": _result_trace(results)})
        return results

    def edit_images(self, request: AiImageRequest) -> list[dict[str, Any]]:
        client = self._client(request.model, request.timeout_seconds)
        model_name = ai_model_config.model_name(request.model) or "gpt-image-1"
        provider_name = self._provider_name(request.model)
        generated: list[dict[str, Any]] = []
        source_notes: list[str] = []

        for item in request.images:
            source_path = _local_source_path(request.app_dir, item)
            if not source_path:
                continue
            source_id = str(item.get("id") or "").strip()
            trace_payload = {
                "model": model_name,
                "image": str(source_path),
                "prompt": request.prompt,
                "n": 1,
                "size": request.size,
                "quality": request.quality,
            }
            if request.conversation:
                request.conversation.emit_custom(
                    "provider.request",
                    {
                        "operation": "images.edit",
                        "source_image_id": source_id,
                        "provider_payload": trace_payload,
                    },
                )
            try:
                with source_path.open("rb") as image_file:
                    payload = {**trace_payload, "image": image_file}
                    response = _call_image_method(client.images.edit, payload)
                generated.extend(
                    _provider_results_from_response(response, provider_name, request.mode or "edit", source_id)
                )
            except Exception as exc:
                if request.conversation:
                    request.conversation.emit_custom(
                        "provider.source_error",
                        {"source_image_id": source_id, "message": str(exc)},
                    )
            note = " | ".join(
                value
                for value in [
                    source_id,
                    str(item.get("usage") or "").strip(),
                    str(item.get("url") or item.get("path") or "").strip(),
                ]
                if value
            )
            if note:
                source_notes.append(note)

        if generated:
            if request.conversation:
                request.conversation.emit_custom("provider.response", {"images": _result_trace(generated)})
            return generated
        if not source_notes:
            return []

        fallback_prompt = request.prompt + "\n\nReference images selected in ERP:\n" + "\n".join(
            f"- {note}" for note in source_notes[:4]
        )
        fallback_request = AiImageRequest(
            app_dir=request.app_dir,
            model=request.model,
            prompt=fallback_prompt,
            images=[],
            mode=request.mode,
            timeout_seconds=request.timeout_seconds,
            size=request.size,
            quality=request.quality,
            count=request.count,
            conversation=request.conversation,
        )
        if request.conversation:
            request.conversation.emit_custom(
                "provider.fallback",
                {"from": "images.edit", "to": "images.generate"},
            )
        return self.generate_images(fallback_request)


__all__ = ["OpenAIImageProvider"]

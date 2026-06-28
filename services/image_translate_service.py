"""Image translation/conversion helpers for marketplace product images.

The real image provider is intentionally injectable so tests can exercise the
whole workflow without network calls. In production this module currently acts
as a safe local materializer: it validates image-AI configuration, accepts image
bytes returned by a provider callback, stores them under ``data/images``, and
returns normalized image-pool items.
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import ai_gateway, ai_model_config, image_service

Provider = Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_TARGET_LANGUAGE = "Spanish (Mexico)"
DEFAULT_IMAGE_SIZE = "1024x1024"
MAX_PROVIDER_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_AI_TIMEOUT_SECONDS = int(os.environ.get("AI_IMAGE_REQUEST_TIMEOUT_SECONDS", "180"))


def service_status() -> dict[str, str]:
    return {"service": "image_translate", "status": "ready"}


def build_translate_prompt(
    product: dict[str, Any],
    target_language: str = DEFAULT_TARGET_LANGUAGE,
    mode: str = "translate",
) -> str:
    """Build the prompt used to ask an image model to localize product images."""
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    title = str(product.get("name") or product.get("title") or source.get("title") or "").strip()
    target = str(target_language or DEFAULT_TARGET_LANGUAGE).strip() or DEFAULT_TARGET_LANGUAGE
    mode_label = "translate existing text" if str(mode or "").strip().lower() == "translate" else "localized ecommerce conversion"
    return "\n".join(
        [
            "Localize ecommerce product images.",
            f"Mode: {mode_label}.",
            f"Target language: {target}.",
            f"Product title: {title}.",
            "Keep the original product, composition, colors, aspect ratio, and selling-point hierarchy.",
            "Only replace or localize text that is already part of the product image layout.",
            "Do not add logos, watermarks, QR codes, fake certifications, prices, or unsupported accessories.",
            "Return independent square marketplace-ready images.",
        ]
    )


def select_source_images(
    product: dict[str, Any],
    image_ids: list[str] | None = None,
    platform: str = "",
) -> list[dict[str, Any]]:
    """Pick source image-pool items to send to an image conversion provider."""
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    pool = source.get("image_pool") if isinstance(source.get("image_pool"), list) else []
    items = image_service.normalize_pool(pool)
    selected_ids = {str(item).strip() for item in (image_ids or []) if str(item).strip()}
    if selected_ids:
        items = [item for item in items if str(item.get("id") or "") in selected_ids]
    platform_key = str(platform or "").strip().lower()
    if platform_key:
        platform_items = [
            item
            for item in items
            if platform_key in [str(value).strip().lower() for value in (item.get("platforms") or [])]
        ]
        if platform_items:
            items = platform_items
    selected_items = [item for item in items if item.get("selected")]
    return selected_items or items


def _bytes_from_result(result: dict[str, Any]) -> tuple[bytes, str]:
    raw = result.get("bytes")
    if isinstance(raw, bytes):
        suffix = str(result.get("suffix") or result.get("ext") or "").strip().lower()
        return raw, suffix or ".png"

    data_url = str(result.get("data_url") or result.get("dataUrl") or "").strip()
    if data_url:
        raw_bytes, suffix = image_service.decode_upload({"data_url": data_url})
        return raw_bytes, suffix

    b64 = str(result.get("b64_json") or result.get("base64") or "").strip()
    if b64:
        try:
            return base64.b64decode(b64), str(result.get("suffix") or ".png")
        except (binascii.Error, ValueError):
            return b"", ".png"

    path = str(result.get("path") or result.get("local_path") or "").strip()
    if path:
        candidate = Path(path)
        if candidate.exists() and candidate.is_file():
            return candidate.read_bytes(), candidate.suffix or ".png"

    url = str(result.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        request = urllib.request.Request(url, headers={"User-Agent": "champion-erp-image-ai/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=IMAGE_AI_TIMEOUT_SECONDS) as response:
                content_type = str(response.headers.get("Content-Type") or "")
                raw = response.read(MAX_PROVIDER_IMAGE_BYTES + 1)
            if len(raw) <= MAX_PROVIDER_IMAGE_BYTES:
                suffix = result.get("suffix") or result.get("ext") or ""
                return raw, _safe_suffix(str(suffix), content_type)
        except Exception:
            return b"", ".png"
    return b"", ".png"


def _response_items(response: Any) -> list[Any]:
    if isinstance(response, dict):
        data = response.get("data")
    else:
        data = getattr(response, "data", None)
    return data if isinstance(data, list) else []


def _item_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _provider_results_from_response(response: Any, provider_name: str, mode: str, source_id: str = "") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in _response_items(response):
        b64_json = str(_item_value(item, "b64_json") or "").strip()
        data_url = str(_item_value(item, "data_url") or _item_value(item, "dataUrl") or "").strip()
        url = str(_item_value(item, "url") or "").strip()
        if not any((b64_json, data_url, url)):
            continue
        result = {
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


def openai_image_provider(config: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
    """Call an OpenAI-compatible image model and normalize returned images."""
    api_key = ai_model_config.model_api_key(config)
    if not api_key:
        return []
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK is not installed. Run: pip install openai") from exc

    base_url = ai_model_config.model_base_url(config).rstrip("/")
    provider_name = str(config.get("provider") or config.get("name") or "OpenAI-Compatible").strip() or "OpenAI-Compatible"
    model = ai_model_config.model_name(config) or "gpt-image-1"
    quality = str(config.get("quality") or "medium").strip()
    size = str(config.get("size") or DEFAULT_IMAGE_SIZE).strip() or DEFAULT_IMAGE_SIZE
    prompt = str(request.get("prompt") or "").strip()
    app_dir = Path(str(request.get("app_dir") or "."))
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": IMAGE_AI_TIMEOUT_SECONDS,
        "default_headers": {"User-Agent": ai_model_config.AI_HTTP_USER_AGENT},
    }
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    generated: list[dict[str, Any]] = []
    has_local_source = False
    selected = [item for item in request.get("images") or [] if isinstance(item, dict)]
    for item in selected:
        source_path = _local_source_path(app_dir, item)
        if not source_path:
            continue
        has_local_source = True
        source_id = str(item.get("id") or "").strip()
        try:
            with source_path.open("rb") as image_file:
                payload: dict[str, Any] = {
                    "model": model,
                    "image": image_file,
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "quality": quality,
                }
                response = _call_image_method(client.images.edit, payload)
            generated.extend(_provider_results_from_response(response, provider_name, str(request.get("mode") or "translate"), source_id))
        except Exception:
            continue

    if generated:
        return generated
    if not has_local_source:
        return []

    source_notes = []
    for item in selected[:4]:
        note = " | ".join(
            value
            for value in [
                str(item.get("id") or "").strip(),
                str(item.get("usage") or "").strip(),
                str(item.get("url") or item.get("path") or "").strip(),
            ]
            if value
        )
        if note:
            source_notes.append(note)
    fallback_prompt = prompt
    if source_notes:
        fallback_prompt = f"{prompt}\n\nReference images selected in ERP:\n" + "\n".join(f"- {note}" for note in source_notes)
    payload = {
        "model": model,
        "prompt": fallback_prompt,
        "n": 1,
        "size": size,
        "quality": quality,
    }
    response = _call_image_method(client.images.generate, payload)
    return _provider_results_from_response(response, provider_name, str(request.get("mode") or "translate"))


def _safe_suffix(suffix: str, mime: str = "") -> str:
    text = str(suffix or "").strip().lower()
    if text and not text.startswith("."):
        text = f".{text}"
    if text == ".jpe":
        text = ".jpg"
    if text not in SUPPORTED_SUFFIXES and mime:
        text = mimetypes.guess_extension(mime.split(";", 1)[0].strip().lower()) or ".png"
    if text == ".jpe":
        text = ".jpg"
    return text if text in SUPPORTED_SUFFIXES else ".png"


def _store_translated_item(
    app_dir: Path | str,
    result: dict[str, Any],
    product_id: str,
    index: int,
    target_language: str,
    platform_values: list[str],
    source_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw, suffix = _bytes_from_result(result)
    if not raw:
        raise RuntimeError("图片翻译结果为空，未收到图片数据")
    suffix = _safe_suffix(suffix, str(result.get("mime") or result.get("content_type") or ""))
    target_dir = image_service.images_root(app_dir) / image_service.safe_segment(product_id, "translated") / "translated"
    target_dir.mkdir(parents=True, exist_ok=True)
    source_id = str((source_item or {}).get("id") or result.get("source_id") or index + 1).strip()
    filename = image_service.safe_filename(
        str(result.get("filename") or f"translated_{index + 1}_{source_id}{suffix}"),
        suffix,
    )
    dest = target_dir / filename
    dest.write_bytes(raw)
    width, height = image_service.image_dimensions(dest)
    rel_path = image_service.relative_to_app(dest, app_dir)
    item = image_service.normalize_item(
        {
            "id": dest.stem,
            "path": rel_path,
            "local_path": rel_path,
            "preview_url": image_service.file_url(dest),
            "origin": "ai_generated",
            "usage": str((source_item or {}).get("usage") or result.get("usage") or ("main" if index == 0 else "detail")),
            "platforms": result.get("platforms") or platform_values,
            "is_main": bool(result.get("is_main", index == 0)),
            "selected": bool(result.get("selected", True)),
            "width": width,
            "height": height,
            "status": "ready",
            "note": f"AI image translation to {target_language}",
            "translated_from_id": source_id,
            "target_language": target_language,
            "provider": result.get("provider", ""),
            "raw": {"image_translate": {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": result.get("mode", "translate")}},
        },
        index,
        app_dir,
    )
    item["translated_from_id"] = source_id
    item["target_language"] = target_language
    item["provider"] = str(result.get("provider") or "").strip()
    return item


def _mock_provider(_: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic local provider used only when explicitly enabled."""
    if os.environ.get("ERP_IMAGE_TRANSLATE_MOCK", "").strip().lower() not in {"1", "true", "yes"}:
        return []
    if image_service.Image is None:
        # 1x1 transparent PNG fallback.
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
        return [{"bytes": tiny_png, "suffix": ".png", "provider": "mock"}]
    from io import BytesIO

    output = BytesIO()
    image_service.Image.new("RGB", (1200, 1200), (245, 245, 245)).save(output, format="PNG")
    return [{"bytes": output.getvalue(), "suffix": ".png", "provider": "mock"}]


def translate_images(
    app_dir: Path | str,
    product: dict[str, Any],
    app_config: dict[str, Any] | None = None,
    target_language: str = DEFAULT_TARGET_LANGUAGE,
    platform: str = "mercadolibre",
    image_ids: list[str] | None = None,
    mode: str = "translate",
    provider: Provider | None = None,
) -> dict[str, Any]:
    """Run image translation/conversion and return image-pool ready items.

    ``provider`` is expected to return one or more dicts containing either
    ``bytes``, ``data_url``, ``b64_json``/``base64``, or a local ``path``.
    """
    config_error = ""
    try:
        cfg = ai_gateway.resolve_model_for_use_case(app_dir, app_config, "image.translate")
    except Exception as exc:
        cfg = {}
        config_error = str(exc)
    provider_name = str(cfg.get("provider") or cfg.get("name") or "OpenAI-Compatible").strip() or "OpenAI-Compatible"
    target = str(target_language or DEFAULT_TARGET_LANGUAGE).strip() or DEFAULT_TARGET_LANGUAGE
    selected = select_source_images(product, image_ids, platform)
    if not selected:
        return {
            "ok": False,
            "error": "没有可翻译的图片，请先采集或上传图片。",
            "imagePoolItems": [],
            "selected_image_ids": image_ids or [],
            "language": target,
            "target_language": target,
            "provider": provider_name,
        }

    prompt = build_translate_prompt(product, target, mode)
    request = {
        "product": product,
        "images": selected,
        "image_ids": [str(item.get("id") or "") for item in selected],
        "target_language": target,
        "language": target,
        "platform": platform,
        "mode": mode,
        "prompt": prompt,
        "app_dir": str(app_dir),
    }
    use_mock = os.environ.get("ERP_IMAGE_TRANSLATE_MOCK", "").strip().lower() in {"1", "true", "yes"}
    if provider is None and not use_mock and not any(_local_source_path(app_dir, item) for item in selected):
        message = "当前图片翻译服务需要本地源图片，请先采集下载或上传图片后再翻译。"
        return {
            "ok": False,
            "message": message,
            "error": message,
            "prompt": prompt,
            "imagePoolItems": [],
            "selected_image_ids": request["image_ids"],
            "language": target,
            "target_language": target,
            "provider": provider_name,
        }
    provider_fn = provider or (_mock_provider if use_mock else openai_image_provider)
    generated = provider_fn(cfg, request)
    if not generated:
        api_key = ai_model_config.model_api_key(cfg)
        if config_error:
            message = f"当前未配置可用图片翻译模型：{config_error}"
        elif not api_key:
            message = "当前未配置图片翻译模型 API Key，请在系统设置中配置后使用。"
        elif provider is None and not use_mock:
            message = "图片模型没有返回图片，请检查模型是否支持图片生成/重绘，并确认 Base URL 使用 OpenAI 兼容的 /v1 地址。"
        else:
            message = "当前图片翻译服务尚未接入真实图片模型。"
        return {
            "ok": False,
            "message": message,
            "error": message,
            "prompt": prompt,
            "imagePoolItems": [],
            "selected_image_ids": request["image_ids"],
            "language": target,
            "target_language": target,
            "provider": provider_name,
        }

    platform_values = image_service.normalize_platforms([platform]) or list(image_service.PLATFORMS)
    product_id = str(product.get("product_id") or product.get("id") or "translated").strip() or "translated"
    items: list[dict[str, Any]] = []
    for index, result in enumerate(generated):
        if not isinstance(result, dict):
            continue
        source_item = selected[index] if index < len(selected) else selected[-1]
        items.append(_store_translated_item(app_dir, result, product_id, index, target, platform_values, source_item))

    return {
        "ok": True,
        "provider": provider_name,
        "language": target,
        "target_language": target,
        "platform": platform,
        "mode": mode,
        "prompt": prompt,
        "selected_image_ids": request["image_ids"],
        "imagePoolItems": items,
        "generated_count": len(items),
    }

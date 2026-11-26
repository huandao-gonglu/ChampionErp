"""商品文案的事实上下文、原生 Agent 生成与格式校验。"""

from __future__ import annotations

from typing import Any
import json

from pydantic_ai import PromptedOutput

from erp_web.schemas.copy import (
    LocalizedCopyOutput,
    MercadoLibreCbtLocalizedCopyOutput,
    localized_copy_output_type,
)
from erp_web.marketplace_registry import (
    default_marketplace_site,
    marketplace_options,
    platform_title_limit,
)
from . import ai_prompt_templates
from .ai_agent_factory import (
    AiAgentExecutionProfile,
    AiAgentFactory,
)
from .ai_model_factory import create_pydantic_model_binding_for_use_case
from .ai_tool_registry import AiToolSet


COPY_TOOLSET = AiToolSet.bind("copy.generate.internal", [], {})
COPY_SCHEMA_RETRIES = 2
COPY_TIMEOUT_SECONDS = 240


def service_status() -> dict[str, str]:
    return {"service": "copy", "status": "ready"}


def normalize_copy_list(value: Any, limit: int | None = None) -> list[str]:
    if isinstance(value, str):
        items = [line.strip() for line in value.replace("；", "\n").replace(";", "\n").splitlines()]
    elif isinstance(value, list):
        items = [str(item or "").strip() for item in value]
    else:
        items = []
    result = []
    seen = set()
    for item in items:
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if limit and len(result) >= limit:
            break
    return result


def product_summary(product: dict[str, Any]) -> str:
    source = product.get("source") if isinstance(product.get("source"), dict) else {}
    fields = {
        "Title": product.get("name") or source.get("title"),
        "Brand": product.get("brand") or source.get("brand"),
        "Model": product.get("model") or source.get("model"),
        "Category": product.get("category") or source.get("category"),
        "Material": ", ".join(normalize_copy_list(product.get("materials") or product.get("source_material"))),
        "Dimensions": product.get("dimensions") or source.get("dimensions"),
        "Weight (kg)": product.get("weight_kg") or product.get("source_weight_kg") or source.get("weight_kg"),
        "Colors": ", ".join(normalize_copy_list(product.get("colors"))),
        "Product attributes": product.get("attributes") or {},
        "Source attributes": source.get("attributes") or {},
        "SKU options": [
            {"name": sku.get("name", ""), "options": sku.get("options") or {}}
            for sku in product.get("sku_items", []) if isinstance(sku, dict)
        ],
        "Package includes": "; ".join(normalize_copy_list(product.get("package_includes"), 8)),
    }
    return json.dumps({key: value for key, value in fields.items() if value}, ensure_ascii=False)


def _market_label(target_market: str) -> str:
    target = str(target_market or "").strip().lower()
    option = next((item for item in marketplace_options() if item["key"] == target), None)
    return str(option["label"] if option else target_market or "marketplace")


def _default_language(target_market: str) -> str:
    return str(default_marketplace_site(target_market).get("language") or "English").strip()


def _requires_cbt_global_title(
    product: dict[str, Any],
    target_market: str,
) -> bool:
    """CBT 草稿除本地化标题外，还需要独立的英文根标题。"""

    if str(target_market or "").strip().lower() != "mercadolibre":
        return False
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    draft = drafts.get("mercadolibre") if isinstance(drafts.get("mercadolibre"), dict) else {}
    candidates = [product, draft]
    for candidate in candidates:
        platform = str(candidate.get("platform") or "").strip().lower()
        site = str(candidate.get("site") or "").strip().upper()
        if site == "CBT" and platform in {"", "mercadolibre"}:
            return True
        target_sites = candidate.get("target_sites")
        if not isinstance(target_sites, list):
            continue
        if any(
            isinstance(target, dict)
            and str(target.get("platform") or "mercadolibre").strip().lower()
            == "mercadolibre"
            and str(target.get("site") or "").strip().upper() == "CBT"
            for target in target_sites
        ):
            return True
    return False


def build_copy_prompt_from_config(
    app_dir: str,
    app_config: dict[str, Any] | None,
    product: dict[str, Any],
    target_market: str,
    language: str,
    mode: str,
) -> dict[str, str]:
    title_limit = platform_title_limit(target_market)
    market_label = _market_label(target_market)
    pair = ai_prompt_templates.load_ai_use_case_prompt_pair("copy.generate")
    configured_user = pair["user"]
    required_context_markers = (
        ("{$language}", "{language}"),
        ("{$market_label}", "{market_label}"),
        ("{$product_summary}", "{product_summary}"),
    )
    has_required_context = all(any(marker in configured_user for marker in alternatives) for alternatives in required_context_markers)
    configured_system = pair["system"].strip()
    if not configured_system:
        raise RuntimeError("文案生成的系统提示词缺失，请检查应用安装文件。")
    if not has_required_context:
        raise RuntimeError("文案生成的系统模板缺少 language、market_label 或 product_summary 上下文。")
    user_prompt = ai_prompt_templates.render_prompt_template(
        configured_user,
        {
            "language": language,
            "target_market": target_market,
            "market_label": market_label,
            "mode": mode,
            "title_limit": title_limit,
            "product_summary": product_summary(product),
        },
    )
    return {
        "system": configured_system,
        "user": user_prompt,
    }


def _normalized_generated_copy(
    parsed: LocalizedCopyOutput,
) -> dict[str, Any]:
    values = parsed.model_dump(mode="json")
    title = str(values.get("title") or "").strip()
    result = {
        "title": title,
        "description": str(values.get("description") or "").strip(),
        "alt_titles": normalize_copy_list(values.get("alt_titles"), 3),
        "search_keywords": normalize_copy_list(values.get("search_keywords"), 20),
    }
    if isinstance(parsed, MercadoLibreCbtLocalizedCopyOutput):
        result["global_title"] = parsed.global_title
    return result


def generate_copy(
    app_dir: str,
    product: dict[str, Any],
    app_config: dict[str, Any] | None = None,
    target_market: str = "mercadolibre",
    language: str = "",
    mode: str = "rewrite",
) -> dict[str, Any]:
    target = str(target_market or "mercadolibre").strip().lower()
    language = language or _default_language(target)
    require_global_title = _requires_cbt_global_title(product, target)
    model = {}
    try:
        prompt_pair = build_copy_prompt_from_config(app_dir, app_config, product, target, language, mode)
        binding = create_pydantic_model_binding_for_use_case(
            app_dir, app_config, "copy.generate",
            timeout_seconds=COPY_TIMEOUT_SECONDS,
        )
        model = {
            "id": binding.model_id,
            "provider": binding.model_config.get("provider") or binding.provider_id,
        }
        output_type = localized_copy_output_type(
            platform_title_limit(target),
            require_global_title=require_global_title,
        )
        factory = AiAgentFactory(
            app_dir=app_dir,
            app_config=app_config,
            model_binding_factory=lambda *_args, **_kwargs: binding,
        )
        outcome = factory.run_sync(
            profile=AiAgentExecutionProfile(
                use_case_id="copy.generate",
                output_type=PromptedOutput(output_type),
                toolset_id=COPY_TOOLSET.toolset_id,
                budget_profile="copy.generate.default",
                permissions=frozenset(),
                timeout_seconds=COPY_TIMEOUT_SECONDS,
                max_model_requests=COPY_SCHEMA_RETRIES + 1,
                max_tool_calls=1,
                max_tool_output_bytes=64 * 1024,
                retries=COPY_SCHEMA_RETRIES,
            ),
            instructions=prompt_pair["system"],
            user_prompt=prompt_pair["user"],
            toolset=COPY_TOOLSET,
            business_scope={
                "product_id": str(product.get("product_id") or ""),
                "draft_id": str(product.get("current_draft_id") or ""),
                "platform": target,
            },
        )
        result = _normalized_generated_copy(outcome.output)
    except Exception as exc:
        from .ai_agent_factory import AiAgentExecutionError
        from .ai_model_errors import direct_model_error_payload

        error_code = ""
        error_details: dict[str, Any] = {}
        retryable = True
        if isinstance(exc, AiAgentExecutionError):
            error_code = exc.code
            error_details = dict(exc.details or {})
            retryable = exc.retryable
        else:
            direct_error = direct_model_error_payload(
                exc,
                use_case_id="copy.generate",
                model_id=str(model.get("id") or ""),
            )
            if direct_error is not None:
                error_code = str(direct_error.get("code") or "")
                error_details = dict(direct_error.get("details") or {})
                retryable = bool(direct_error.get("retryable", False))
        payload: dict[str, Any] = {
            "ok": False,
            "provider": str(model.get("provider") or ""),
            "ai_model_id": str(model.get("id") or ""),
            "target_market": target,
            "language": language,
            "mode": mode,
            "copy": {},
            "error": f"本地化文案生成失败：{exc}",
        }
        if error_code:
            payload["error_code"] = error_code
        if error_details:
            payload["error_details"] = error_details
        if not retryable:
            payload["retryable"] = False
        return payload
    return {
        "ok": True,
        "provider": str(model.get("provider") or ""),
        "ai_model_id": str(model.get("id") or ""),
        "target_market": target,
        "language": language,
        "mode": mode,
        "copy": result,
    }

"""API 模型目录发现边界。

模型目录查询不是推理请求；这里按 Provider Catalog 的发现策略，复用 Pydantic
Provider 持有的异步 client，避免重新引入 SDK/HTTP 认证旁路。
"""

from __future__ import annotations

import asyncio

from . import ai_provider_catalog


class AiModelDiscoveryError(RuntimeError):
    """模型目录查询失败；不暴露 SDK request、response body 或凭据。"""


async def _list_remote_models(
    provider_id: str,
    base_url: str,
    api_key: str,
    timeout: int | float,
) -> list[dict[str, str]]:
    spec = ai_provider_catalog.provider_spec(provider_id)
    if spec.discovery_strategy not in {
        "openai_models",
        "anthropic_models",
        "google_models",
    }:
        raise AiModelDiscoveryError(
            f"AI Provider {spec.label} 不提供远端模型目录，请手动填写模型 ID。"
        )
    provider = ai_provider_catalog.create_pydantic_provider(
        provider_id,
        base_url=base_url,
        api_key=api_key,
    )
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    async with provider:
        if spec.discovery_strategy == "google_models":
            aio_client = getattr(provider.client, "aio", None)
            models_api = getattr(aio_client, "models", None)
            if models_api is None or not callable(getattr(models_api, "list", None)):
                raise AiModelDiscoveryError(
                    f"AI Provider {spec.label} 不提供远端模型目录，请手动填写模型 ID。"
                )

            async def _collect_google_models() -> list[str]:
                ids: list[str] = []
                pager = await models_api.list()
                async for item in pager:
                    raw_name = str(getattr(item, "name", "") or "").strip()
                    if raw_name.startswith("models/"):
                        raw_name = raw_name.split("/", 1)[1]
                    if raw_name:
                        ids.append(raw_name)
                return ids

            model_ids = await asyncio.wait_for(
                _collect_google_models(),
                timeout=float(timeout),
            )
        else:
            models_api = getattr(provider.client, "models", None)
            if models_api is None or not callable(getattr(models_api, "list", None)):
                raise AiModelDiscoveryError(
                    f"AI Provider {spec.label} 不提供远端模型目录，请手动填写模型 ID。"
                )
            response = await models_api.list(
                timeout=float(timeout),
            )
            items = (
                response.data
                if isinstance(getattr(response, "data", None), list)
                else []
            )
            model_ids = [
                str(getattr(item, "id", "") or "").strip() for item in items
            ]
    for model_id in model_ids:
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        options.append({"id": model_id, "name": model_id})
    return options


def list_remote_models(
    provider_id: str,
    base_url: str,
    api_key: str,
    timeout: int | float = 60,
) -> list[dict[str, str]]:
    """通过集中 Provider 客户端读取可选模型，不执行 AI 推理。"""

    try:
        return asyncio.run(
            _list_remote_models(provider_id, base_url, api_key, timeout)
        )
    except AiModelDiscoveryError:
        raise
    except ValueError as exc:
        raise AiModelDiscoveryError(str(exc)) from None
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            raise AiModelDiscoveryError(
                f"远端模型目录查询失败：HTTP {status_code}。"
            ) from None
        raise AiModelDiscoveryError(
            "远端模型目录连接失败。"
        ) from None


__all__ = ["AiModelDiscoveryError", "list_remote_models"]

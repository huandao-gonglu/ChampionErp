#!/usr/bin/env python3
"""通过生产同款 Pydantic Model 边界探测已配置的 AI API。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from erp_web.app_config import normalize_app_config  # noqa: E402
from erp_web.services import (  # noqa: E402
    ai_direct_request_service,
    ai_model_config,
    config_service,
)
from erp_web.services.ai_model_discovery import list_remote_models  # noqa: E402
from erp_web.services.ai_model_factory import create_pydantic_model_binding  # noqa: E402


DEFAULT_PROMPT = 'Return one compact JSON object exactly like {"ok":true,"message":"pong"}.'


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit(f"Config must be a JSON object: {path}")
    return payload


def default_config_path() -> Path:
    for path in (
        ROOT_DIR / "config" / "app_config.json",
        ROOT_DIR / "config" / "ai_config.snapshot.json",
    ):
        if path.exists():
            return path
    raise SystemExit("No config/app_config.json or config/ai_config.snapshot.json found.")


def normalized_config(path: Path) -> dict[str, Any]:
    config_service.load_env(ROOT_DIR)
    return normalize_app_config(load_json(path))


def model_by_id(config: dict[str, Any], model_id: str) -> dict[str, Any]:
    models = ai_model_config.normalize_ai_models(config.get("ai_models"))
    for model in models:
        if str(model.get("id") or "") == model_id:
            return model
    available = ", ".join(str(model.get("id") or "") for model in models)
    raise SystemExit(f"AI model not found: {model_id}. Available: {available}")


def default_model_id(config: dict[str, Any]) -> str:
    for model in ai_model_config.normalize_ai_models(config.get("ai_models")):
        capabilities = set(
            ai_model_config.normalize_capabilities(model.get("capabilities"))
        )
        if ai_model_config.CAP_WEB_SEARCH in capabilities:
            return str(model.get("id") or "")
    models = ai_model_config.normalize_ai_models(config.get("ai_models"))
    return str(models[0].get("id") or "") if models else ""


def print_model_summary(model: dict[str, Any]) -> None:
    print("== Model ==")
    print(f"id: {model.get('id')}")
    print(f"provider: {model.get('provider')}")
    print(f"api_style: {ai_model_config.normalize_api_style(model.get('api_style'))}")
    print(f"base_url: {ai_model_config.model_base_url(model)}")
    print(f"model: {ai_model_config.model_name(model)}")
    print(
        "capabilities: "
        + ", ".join(ai_model_config.normalize_capabilities(model.get("capabilities")))
    )
    print(f"api_key: {config_service.mask_secret(ai_model_config.model_api_key(model))}")


def run_models(model: dict[str, Any], timeout: int, max_chars: int) -> bool:
    try:
        models = list_remote_models(
            ai_model_config.model_base_url(model),
            ai_model_config.model_api_key(model),
            timeout,
        )
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        return False
    text = json.dumps(models, ensure_ascii=False, indent=2)
    print("\n== Pydantic Provider model discovery ==")
    print(text[:max_chars])
    if len(text) > max_chars:
        print("...<truncated>")
    return True


def run_request(
    *,
    model: dict[str, Any],
    prompt: str,
    include_web_search: bool,
    stream: bool,
    timeout: int,
    max_chars: int,
    dry_run: bool,
) -> bool:
    required = [ai_model_config.CAP_CHAT, ai_model_config.CAP_JSON]
    if include_web_search:
        required.append(ai_model_config.CAP_WEB_SEARCH)
    if dry_run:
        binding = create_pydantic_model_binding(
            model,
            generation_settings={"temperature": 0},
            timeout_seconds=timeout,
            required_capabilities=required,
        )
        print("\n== Pydantic Direct Model dry-run ==")
        print(
            json.dumps(
                {
                    "model_id": binding.model_id,
                    "model_name": binding.model_name,
                    "provider_id": binding.provider_id,
                    "api_style": binding.api_style,
                    "required_capabilities": required,
                    "model_settings": dict(binding.model_settings),
                    "stream": stream,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return True
    deltas: list[str] = []
    try:
        result = ai_direct_request_service.chat_json(
            app_dir=ROOT_DIR,
            use_case_id="script.test_ai_api",
            model=model,
            required_capabilities=required,
            messages=[{"role": "user", "content": prompt}],
            generation_settings={"temperature": 0},
            temperature=0,
            max_tokens=None,
            timeout_seconds=timeout,
            response_format=True,
            stream=stream,
            token_callback=deltas.append if stream else None,
        )
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        return False
    print("\n== Pydantic Direct Model result ==")
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text[:max_chars])
    if stream:
        print(f"streamed characters: {len(''.join(deltas))}")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test a configured AI API through the production Pydantic boundary."
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--model-id", default="")
    parser.add_argument("--model-name", default="", help="受控覆盖配置中的模型名。")
    parser.add_argument(
        "--case",
        choices=["plain", "web-search", "stream", "models", "full"],
        default="plain",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-chars", type=int, default=4000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = normalized_config(args.config)
    model_id = args.model_id or default_model_id(config)
    if not model_id:
        raise SystemExit("No AI model id could be resolved.")
    model = model_by_id(config, model_id)
    if args.model_name:
        model = {**model, "model": args.model_name}
    if ai_model_config.model_connection_type(model) != ai_model_config.CONNECTION_TYPE_API:
        raise SystemExit("This script only probes connection_type=api models.")
    if not ai_model_config.model_api_key(model):
        raise SystemExit(f"AI model {model_id} has no API key.")
    if not ai_model_config.model_base_url(model):
        raise SystemExit(f"AI model {model_id} has no base_url.")
    if not ai_model_config.model_name(model):
        raise SystemExit(f"AI model {model_id} has no model name.")

    print(f"config: {args.config}")
    print_model_summary(model)
    ok = True
    if args.case in {"models", "full"}:
        ok = run_models(model, args.timeout, args.max_chars)
    if args.case != "models":
        include_web_search = args.case in {"web-search", "stream", "full"}
        stream = args.case in {"stream", "full"}
        ok = run_request(
            model=model,
            prompt=args.prompt,
            include_web_search=include_web_search,
            stream=stream,
            timeout=args.timeout,
            max_chars=args.max_chars,
            dry_run=args.dry_run,
        ) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

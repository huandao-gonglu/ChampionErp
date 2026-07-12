#!/usr/bin/env python3
"""Probe a configured AI API endpoint without printing secrets."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from erp_web.app_config import normalize_app_config  # noqa: E402
from erp_web.services import ai_model_config, config_service  # noqa: E402


DEFAULT_PROMPT = 'Return one compact JSON object exactly like {"ok":true,"message":"pong"}.'


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit(f"Config must be a JSON object: {path}")
    return payload


def default_config_path() -> Path:
    for path in (ROOT_DIR / "config" / "app_config.json", ROOT_DIR / "config" / "ai_config.snapshot.json"):
        if path.exists():
            return path
    raise SystemExit("No config/app_config.json or config/ai_config.snapshot.json found.")


def normalize_config(path: Path) -> dict[str, Any]:
    config_service.load_env(ROOT_DIR)
    return normalize_app_config(load_json(path))


def chat_completions_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if text.endswith("/chat/completions"):
        return text
    return f"{text}/chat/completions"


def responses_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if text.endswith("/responses"):
        return text
    for suffix in ("/chat/completions", "/images/generations", "/images/edits"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return f"{text}/responses"


def models_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/images/generations", "/images/edits"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return f"{text}/models"


def mask_secret(value: str) -> str:
    return config_service.mask_secret(value)


def compact_json(value: Any, max_chars: int = 1200) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return text if len(text) <= max_chars else text[:max_chars] + "\n...<truncated>"


def sanitize(text: str, secrets: list[str]) -> str:
    result = str(text or "")
    for secret in secrets:
        if secret:
            result = result.replace(secret, mask_secret(secret))
    result = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", result, flags=re.IGNORECASE)
    result = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-***", result)
    return result


def model_by_id(config: dict[str, Any], model_id: str) -> dict[str, Any]:
    models = ai_model_config.normalize_ai_models(config.get("ai_models"))
    for model in models:
        if str(model.get("id") or "") == model_id:
            return model
    available = ", ".join(str(model.get("id") or "") for model in models)
    raise SystemExit(f"AI model not found: {model_id}. Available: {available}")


def default_model_id(config: dict[str, Any]) -> str:
    research = config.get("product_research") if isinstance(config.get("product_research"), dict) else {}
    providers = research.get("search_providers") if isinstance(research.get("search_providers"), list) else []
    for provider in providers:
        record = provider if isinstance(provider, dict) else {}
        cfg = record.get("config_json") if isinstance(record.get("config_json"), dict) else {}
        if str(cfg.get("provider_strategy") or "") == "ai_web_search":
            model_id = str(cfg.get("ai_model_id") or "").strip()
            if model_id:
                return model_id
    for model in ai_model_config.normalize_ai_models(config.get("ai_models")):
        capabilities = set(ai_model_config.normalize_capabilities(model.get("capabilities")))
        if ai_model_config.CAP_WEB_SEARCH in capabilities:
            return str(model.get("id") or "")
    models = ai_model_config.normalize_ai_models(config.get("ai_models"))
    return str(models[0].get("id") or "") if models else ""


def research_stream_enabled(config: dict[str, Any], model_id: str) -> bool:
    research = config.get("product_research") if isinstance(config.get("product_research"), dict) else {}
    providers = research.get("search_providers") if isinstance(research.get("search_providers"), list) else []
    for provider in providers:
        record = provider if isinstance(provider, dict) else {}
        cfg = record.get("config_json") if isinstance(record.get("config_json"), dict) else {}
        if str(cfg.get("provider_strategy") or "") != "ai_web_search":
            continue
        if model_id and str(cfg.get("ai_model_id") or "").strip() not in ("", model_id):
            continue
        return bool(cfg.get("stream", True))
    return True


def web_search_body(api_style: str, model: dict[str, Any]) -> dict[str, Any]:
    extra = model.get("extra") if isinstance(model.get("extra"), dict) else {}
    if api_style == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        return {"tools": extra.get("web_search_tools") or [{"type": "web_search"}]}
    return {"web_search_options": extra.get("web_search_options") or {"search_context_size": "medium"}}


def request_body(
    *,
    api_style: str,
    model: dict[str, Any],
    model_name: str,
    prompt: str,
    stream: bool,
    include_web_search: bool,
) -> tuple[str, dict[str, Any]]:
    if api_style == ai_model_config.API_STYLE_OPENAI_RESPONSES:
        body: dict[str, Any] = {
            "model": model_name,
            "input": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": stream,
        }
        if include_web_search:
            body.update(web_search_body(api_style, model))
        return responses_url(ai_model_config.model_base_url(model)), body
    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "stream": stream,
    }
    if include_web_search:
        body.update(web_search_body(api_style, model))
    return chat_completions_url(ai_model_config.model_base_url(model)), body


def print_model_summary(model: dict[str, Any], api_key: str, api_style: str) -> None:
    print("== Model ==")
    print(f"id: {model.get('id')}")
    print(f"provider: {model.get('provider')}")
    print(f"api_style: {api_style}")
    print(f"base_url: {ai_model_config.model_base_url(model)}")
    print(f"model: {ai_model_config.model_name(model)}")
    print(f"capabilities: {', '.join(ai_model_config.normalize_capabilities(model.get('capabilities')))}")
    source = "api_key" if str(model.get("api_key") or "").strip() else f"env:{model.get('api_key_env') or ''}"
    print(f"api_key: {mask_secret(api_key)} ({source})")


def print_response_body(raw: bytes, secrets: list[str], max_chars: int) -> None:
    text = raw.decode("utf-8", errors="replace") if raw else ""
    print(sanitize(text[:max_chars], secrets))
    if len(text) > max_chars:
        print("...<truncated>")


def run_get_models(*, model: dict[str, Any], api_key: str, timeout: int, max_chars: int, secrets: list[str]) -> bool:
    url = models_url(ai_model_config.model_base_url(model))
    print("\n== GET /models ==")
    print(f"GET {url}")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            print(f"HTTP {response.status} {response.reason}")
            print_response_body(response.read(), secrets, max_chars)
            return 200 <= int(response.status) < 300
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.reason}")
        print_response_body(exc.read(), secrets, max_chars)
        return False
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        return False


def run_post_case(
    *,
    label: str,
    model: dict[str, Any],
    api_key: str,
    api_style: str,
    model_name: str,
    prompt: str,
    stream: bool,
    include_web_search: bool,
    timeout: int,
    max_chars: int,
    secrets: list[str],
    dry_run: bool,
) -> bool:
    url, body = request_body(
        api_style=api_style,
        model=model,
        model_name=model_name,
        prompt=prompt,
        stream=stream,
        include_web_search=include_web_search,
    )
    print(f"\n== {label} ==")
    print(f"POST {url}")
    print(f"stream: {stream}")
    print(f"web_search: {include_web_search}")
    print("body:")
    print(compact_json(body))
    if dry_run:
        print("dry-run: request not sent")
        return True
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": ai_model_config.AI_HTTP_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            print(f"HTTP {response.status} {response.reason}")
            if stream:
                lines: list[str] = []
                for index, raw_line in enumerate(response):
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    lines.append(line)
                    if index >= 40 or sum(len(item) for item in lines) >= max_chars:
                        break
                print(sanitize("\n".join(lines), secrets))
            else:
                print_response_body(response.read(), secrets, max_chars)
            return 200 <= int(response.status) < 300
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.reason}")
        print_response_body(exc.read(), secrets, max_chars)
        return False
    except Exception as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test a configured AI API endpoint.")
    parser.add_argument("--config", type=Path, default=default_config_path(), help="Config JSON path.")
    parser.add_argument("--model-id", default="", help="AI model id. Defaults to product_research AI search model.")
    parser.add_argument(
        "--request-type",
        choices=["auto", "chat", "responses"],
        default="auto",
        help="auto uses the saved model api_style; chat uses /chat/completions; responses uses /responses.",
    )
    parser.add_argument("--api-style", choices=["auto", *ai_model_config.AI_API_STYLES], default="auto")
    parser.add_argument("--model-name", default="", help="Override model name for the request.")
    parser.add_argument(
        "--case",
        choices=["research", "plain", "web-search", "stream", "models", "full"],
        default="research",
        help="research mirrors the product-research AI search request.",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-chars", type=int, default=4000)
    parser.add_argument("--dry-run", action="store_true", help="Print the request without sending it.")
    return parser.parse_args()


def resolved_api_style(args: argparse.Namespace, model: dict[str, Any]) -> str:
    if args.api_style != "auto":
        return args.api_style
    if args.request_type == "chat":
        return ai_model_config.API_STYLE_OPENAI_COMPATIBLE
    if args.request_type == "responses":
        return ai_model_config.API_STYLE_OPENAI_RESPONSES
    return ai_model_config.normalize_api_style(model.get("api_style"))


def main() -> int:
    args = parse_args()
    config = normalize_config(args.config)
    model_id = args.model_id or default_model_id(config)
    if not model_id:
        raise SystemExit("No AI model id could be resolved.")
    model = model_by_id(config, model_id)
    api_key = ai_model_config.model_api_key(model)
    if not api_key:
        raise SystemExit(f"AI model {model_id} has no API key.")
    base_url = ai_model_config.model_base_url(model)
    if not base_url:
        raise SystemExit(f"AI model {model_id} has no base_url.")
    model_name = args.model_name or ai_model_config.model_name(model)
    if not model_name:
        raise SystemExit(f"AI model {model_id} has no model name.")
    api_style = resolved_api_style(args, model)
    secrets = [api_key]

    print(f"config: {args.config}")
    print_model_summary(model, api_key, api_style)

    ok = True
    cases: list[tuple[str, bool, bool]] = []
    if args.case == "models":
        return 0 if run_get_models(model=model, api_key=api_key, timeout=args.timeout, max_chars=args.max_chars, secrets=secrets) else 1
    if args.case == "plain":
        cases.append(("plain chat/responses", False, False))
    elif args.case == "web-search":
        cases.append(("web_search non-stream", False, True))
    elif args.case == "stream":
        cases.append(("web_search stream", True, True))
    elif args.case == "full":
        ok = run_get_models(model=model, api_key=api_key, timeout=args.timeout, max_chars=args.max_chars, secrets=secrets)
        cases.extend(
            [
                ("plain chat/responses", False, False),
                ("web_search non-stream", False, True),
                ("web_search stream", True, True),
            ]
        )
    else:
        stream = research_stream_enabled(config, model_id)
        include_web_search = ai_model_config.CAP_WEB_SEARCH in ai_model_config.normalize_capabilities(model.get("capabilities"))
        cases.append(("product research request", stream, include_web_search))

    for label, stream, include_web_search in cases:
        ok = run_post_case(
            label=label,
            model=model,
            api_key=api_key,
            api_style=api_style,
            model_name=model_name,
            prompt=args.prompt,
            stream=stream,
            include_web_search=include_web_search,
            timeout=args.timeout,
            max_chars=args.max_chars,
            secrets=secrets,
            dry_run=args.dry_run,
        ) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

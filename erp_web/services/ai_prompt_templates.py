"""File-backed prompt templates for global AI use cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import ai_model_config


UNUSED_GLOBAL_PROMPT_USE_CASES = {"copy.preview"}
GLOBAL_PROMPT_USE_CASES = tuple(
    use_case_id
    for use_case_id in ai_model_config.AI_USE_CASES
    if use_case_id not in UNUSED_GLOBAL_PROMPT_USE_CASES
)

DEFAULT_AI_USE_CASE_PROMPTS: dict[str, dict[str, str]] = {
    "copy.generate": {
        "path": "config/prompts/copy_generate.json",
    },
    "image.translate": {
        "path": "config/prompts/image_translate.json",
    },
    "category.attribute_fill": {
        "path": "config/prompts/category_attribute_fill.json",
    },
    "category.attribute_translation": {
        "path": "config/prompts/category_attribute_translation.json",
    },
    "category.result_translation": {
        "path": "config/prompts/category_result_translation.json",
    },
    "research.web_search": {
        "path": "config/prompts/ai_example.json",
    },
}


def default_ai_use_case_prompts() -> dict[str, dict[str, str]]:
    return {use_case_id: dict(config) for use_case_id, config in DEFAULT_AI_USE_CASE_PROMPTS.items()}


def normalize_ai_use_case_prompts(value: Any) -> dict[str, dict[str, str]]:
    raw = value if isinstance(value, dict) else {}
    normalized: dict[str, dict[str, str]] = {}
    for use_case_id, defaults in DEFAULT_AI_USE_CASE_PROMPTS.items():
        incoming = raw.get(use_case_id) if isinstance(raw.get(use_case_id), dict) else {}
        row: dict[str, str] = {}
        path = str(incoming.get("path") or defaults.get("path") or "").strip()
        if path:
            row["path"] = path
        normalized[use_case_id] = row
    return normalized


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_prompt_paths(app_dir: Path | str, prompt_path: str) -> list[Path]:
    path = Path(prompt_path).expanduser()
    if path.is_absolute():
        return [path]
    return [Path(app_dir) / path, _project_root() / path]


def _writable_prompt_path(app_dir: Path | str, prompt_path: str) -> Path:
    path = Path(prompt_path).expanduser()
    if path.is_absolute():
        return path
    return Path(app_dir) / path


def load_prompt_json(app_dir: Path | str, prompt_path: str) -> dict[str, str]:
    if not prompt_path:
        return {"description": "", "system": "", "user": ""}
    for path in _candidate_prompt_paths(app_dir, prompt_path):
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data if isinstance(data, dict) else {}
            return {
                "description": str(raw.get("description") or "").rstrip(),
                "system": str(raw.get("system") or "").rstrip(),
                "user": str(raw.get("user") or "").rstrip(),
            }
    return {"description": "", "system": "", "user": ""}


def write_prompt_json(
    app_dir: Path | str,
    prompt_path: str,
    *,
    description: str = "",
    system: str = "",
    user: str = "",
) -> Path:
    path = _writable_prompt_path(app_dir, prompt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": str(description or "").rstrip(),
        "system": str(system or "").rstrip(),
        "user": str(user or "").rstrip(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def render_prompt_template(template: str, context: dict[str, Any]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{$" + key + "}", str(value))
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered


def load_ai_use_case_prompt_pair(
    app_dir: Path | str,
    app_config: dict[str, Any] | None,
    use_case_id: str,
) -> dict[str, str]:
    config = app_config if isinstance(app_config, dict) else {}
    prompts = normalize_ai_use_case_prompts(config.get("ai_use_case_prompts"))
    row = prompts.get(use_case_id, {})
    prompt = load_prompt_json(app_dir, row.get("path", ""))
    return {
        "system": prompt["system"],
        "user": prompt["user"],
    }


def public_ai_use_case_prompts(app_dir: Path | str, app_config: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    config = app_config if isinstance(app_config, dict) else {}
    prompts = normalize_ai_use_case_prompts(config.get("ai_use_case_prompts"))
    public: dict[str, dict[str, str]] = {}
    for use_case_id, row in prompts.items():
        prompt = load_prompt_json(app_dir, row.get("path", ""))
        public[use_case_id] = {
            **row,
            "description": prompt["description"],
            "system_prompt": prompt["system"],
            "user_prompt": prompt["user"],
        }
    return public


def merge_ai_use_case_prompts(
    app_dir: Path | str,
    current: dict[str, Any],
    incoming: Any,
) -> dict[str, dict[str, str]]:
    current_prompts = normalize_ai_use_case_prompts(current.get("ai_use_case_prompts") if isinstance(current, dict) else None)
    raw = incoming if isinstance(incoming, dict) else {}
    next_prompts = normalize_ai_use_case_prompts({**current_prompts, **raw})
    for use_case_id, row in next_prompts.items():
        incoming_row = raw.get(use_case_id) if isinstance(raw.get(use_case_id), dict) else {}
        if not any(key in incoming_row for key in ("description", "system_prompt", "user_prompt")):
            continue
        prompt_path = row.get("path", "")
        if prompt_path:
            current_prompt = load_prompt_json(app_dir, prompt_path)
            write_prompt_json(
                app_dir,
                prompt_path,
                description=str(incoming_row.get("description", current_prompt["description"]) or ""),
                system=str(incoming_row.get("system_prompt", current_prompt["system"]) or ""),
                user=str(incoming_row.get("user_prompt", current_prompt["user"]) or ""),
            )
    return next_prompts


__all__ = [
    "GLOBAL_PROMPT_USE_CASES",
    "default_ai_use_case_prompts",
    "load_ai_use_case_prompt_pair",
    "load_prompt_json",
    "merge_ai_use_case_prompts",
    "normalize_ai_use_case_prompts",
    "public_ai_use_case_prompts",
    "render_prompt_template",
    "write_prompt_json",
]

# -*- coding: utf-8 -*-
from __future__ import annotations

"""AI 用例统一执行入口。"""

import json
from typing import Any, Callable, TypeVar

from erp_web.context import get_context
from erp_web.services import ai_gateway, ai_prompt_templates

T = TypeVar("T")


def run_ai_use_case(
    use_case_id: str,
    payload: Any,
    normalizer: Callable[[Any], T],
    *,
    temperature: float | None = None,
    **chat_options: Any,
) -> T:
    """加载绑定与提示词、渲染 ``input_json``、执行 JSON 调用并归一化。"""

    context = get_context()
    app_dir = context.paths.app_dir
    app_config = context.config.load_app_config()
    prompt_pair = ai_prompt_templates.load_ai_use_case_prompt_pair(
        app_dir,
        app_config,
        use_case_id,
    )
    messages = [
        {"role": "system", "content": prompt_pair["system"]},
        {
            "role": "user",
            "content": ai_prompt_templates.render_prompt_template(
                prompt_pair["user"],
                {"input_json": json.dumps(payload, ensure_ascii=False)},
            ),
        },
    ]
    if temperature is not None:
        chat_options["temperature"] = temperature
    result = ai_gateway.chat_json(
        app_dir,
        app_config,
        use_case_id,
        messages,
        **chat_options,
    )
    return normalizer(result)


__all__ = ["run_ai_use_case"]

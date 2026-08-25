from __future__ import annotations

"""全局任务补充资料的可信来源标记。

模型可以看到 Capability 请求 Schema，因此仅凭某个字段有值，不能证明它是
用户在任务补充界面明确提交的。Controller 把受信 ``submit_input`` 写入过的
顶层字段名记录到步骤，并在执行时通过 ``business_scope`` 传给 Capability。
"""

import json
from collections.abc import Iterable, Mapping


USER_INPUT_KEYS_SCOPE_KEY = "user_input_keys"


def encode_user_input_keys(keys: Iterable[str]) -> str:
    """把用户补充字段编码为稳定、可放入 business_scope 的 JSON。"""

    normalized = sorted(
        {
            str(key or "").strip()
            for key in keys
            if str(key or "").strip()
        }
    )
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def user_supplied_input(
    business_scope: Mapping[str, str],
    key: str,
) -> bool:
    """判断字段是否来自 Controller 的受信补充资料入口。"""

    expected = str(key or "").strip()
    if not expected:
        return False
    raw = str(business_scope.get(USER_INPUT_KEYS_SCOPE_KEY) or "").strip()
    if not raw:
        return False
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(decoded, list):
        return False
    normalized = {
        str(item or "").strip()
        for item in decoded
        if isinstance(item, str) and str(item or "").strip()
    }
    return expected in normalized


__all__ = [
    "USER_INPUT_KEYS_SCOPE_KEY",
    "encode_user_input_keys",
    "user_supplied_input",
]

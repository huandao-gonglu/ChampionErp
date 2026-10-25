"""只在模型输入副本中复用工具结果的重复 JSON 结构，不删减事实。"""

from collections import Counter
import json
from typing import Any


def share_repeated_json_values(content: Any) -> Any:
    """大结果中的相同集合只发送一次；小结果、无明显收益的结果原样返回。

    不是持久化格式：原生消息、业务返回值和 Python 脚本仍使用原始对象。
    含已有 $ref 的结果不重新编码，避免将业务字段误解释为共享引用。
    """
    if not isinstance(content, (dict, list)):
        return content
    try:
        original = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        # 非 JSON 值继续由原生 Provider 编码，不影响工具执行与请求。
        return content
    if len(original) < 8000 or '"$ref"' in original:
        return content

    counts: Counter[str] = Counter()
    keys: dict[int, str] = {}

    def collect(value: Any) -> None:
        if not isinstance(value, (dict, list)):
            return
        key = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(key) >= 512:
            keys[id(value)] = key
            counts[key] += 1
        for child in value.values() if isinstance(value, dict) else value:
            collect(child)

    collect(content)
    shared: dict[str, Any] = {}
    references: dict[str, str] = {}

    def children(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: encode(child) for key, child in value.items()}
        return [encode(child) for child in value]

    def encode(value: Any) -> Any:
        if not isinstance(value, (dict, list)):
            return value
        key = keys.get(id(value), "")
        if counts[key] > 1:
            if key not in references:
                name = str(len(references))
                references[key] = name
                shared[name] = children(value)
            return {"$ref": f"#/shared_values/{references[key]}"}
        return children(value)

    result = {
        "encoding": "shared_json_values.v1",
        "notice": "value 是完整工具结果；其中仅含 $ref 的对象引用 shared_values 对应项，可递归展开。共享项只省略重复传输，未省略任何记录或字段。",
        "value": encode(content),
        "shared_values": shared,
    }
    if not shared or len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > len(original) * 0.8:
        return content
    return result

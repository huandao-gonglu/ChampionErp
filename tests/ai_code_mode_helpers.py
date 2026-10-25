"""可控模型生成 Python 调用，并读取官方保存的嵌套工具回执。"""

import json
from functools import wraps

from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall


def python_call(*, name, json_args, tool_call_id):
    arguments = json.loads(json_args)
    parameters = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    return DeltaToolCall(
        name="run_code",
        json_args=json.dumps({"code": f"await {name}({parameters})"}),
        tool_call_id=tool_call_id,
    )


def business_returns(messages):
    results = []
    for message in messages:
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name == "search_tools":
                continue
            metadata = part.metadata or {}
            if metadata.get("code_mode"):
                for nested in metadata["tool_returns"].values():
                    results.append(ToolReturnPart(**nested) if isinstance(nested, dict) else nested)
            else:
                results.append(part)
    return results


def available_python_functions(info):
    return next(tool.description for tool in info.function_tools if tool.name == "run_code") + (info.instructions or "")


def discover_tools(info, *names):
    """可控模型先通过真实原生搜索发现本用例需要的工具。"""
    available = {tool.name for tool in info.function_tools}
    missing = [name for name in dict.fromkeys(names) if name not in available]
    if not missing:
        return None
    return DeltaToolCall(name="search_tools", json_args=json.dumps({"queries": missing}), tool_call_id="discover-" + "-".join(missing))


def discovering_tools(*names):
    """领域验收模型先执行一次真实工具发现，再运行已有业务步骤。"""
    def decorate(model):
        @wraps(model)
        async def stream(messages, info):
            if discovery := discover_tools(info, *names):
                yield {0: discovery}
                return
            async for chunk in model(messages, info):
                yield chunk
        return stream
    return decorate

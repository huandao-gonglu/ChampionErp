"""可控模型生成 Python 调用，并读取官方保存的嵌套工具回执。"""

import json

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
            metadata = part.metadata or {}
            if metadata.get("code_mode"):
                for nested in metadata["tool_returns"].values():
                    results.append(ToolReturnPart(**nested) if isinstance(nested, dict) else nested)
            else:
                results.append(part)
    return results


def available_python_functions(info):
    return next(tool.description for tool in info.function_tools if tool.name == "run_code")

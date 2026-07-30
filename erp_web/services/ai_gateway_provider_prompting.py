"""CLI 与浏览器 Provider 共用的纯提示词构造。"""

from __future__ import annotations

def _cli_prompt(
    messages: list[dict[str, str]],
    *,
    response_format: bool,
    allow_external_read: bool = False,
    allow_generated_artifacts: bool = False,
) -> str:
    return _conversation_prompt(
        messages,
        response_format=response_format,
        allow_external_read=allow_external_read,
        allow_generated_artifacts=allow_generated_artifacts,
        channel="cli",
    )

def _conversation_prompt(
    messages: list[dict[str, str]],
    *,
    response_format: bool,
    allow_external_read: bool,
    allow_generated_artifacts: bool,
    channel: str,
) -> str:
    system_parts: list[str] = []
    user_parts: list[str] = []
    assistant_parts: list[str] = []
    for message in messages:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        role = str(message.get("role") or "user").strip().lower()
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            assistant_parts.append(content)
        else:
            user_parts.append(content)

    sections: list[str] = ["请完成下面的任务。"]
    task_text = "\n\n".join(user_parts).strip()
    if task_text:
        sections.append(f"任务：\n{task_text}")

    requirements: list[str] = []
    if allow_generated_artifacts:
        if channel == "browser":
            requirements.append("可以使用当前网页会话可用的图像生成能力；不要返回 SVG、ASCII 图或文字描述来替代真实图片。")
        else:
            requirements.append("可以使用当前会话可用的图像生成工具，并可以把生成的图片保存到默认生成目录；不要修改项目文件或业务数据。")
    elif allow_external_read:
        if channel == "browser":
            requirements.append("可以使用当前网页会话可用的联网或搜索能力，只基于实时验证结果回答。")
        else:
            requirements.append("不要修改文件。允许为完成任务进行只读联网检索或读取公开网页；不要执行会改变外部状态的操作。")
    else:
        if channel == "browser":
            requirements.append("只生成最终答案，不要解释执行过程。")
        else:
            requirements.append("不要修改文件，不要执行外部操作；只生成最终答案。")
    if response_format:
        requirements.append("最终输出必须是一个合法 JSON 对象；不要输出 Markdown 代码块、解释文字或前后缀。")
    requirements.extend(system_parts)
    if requirements:
        sections.append("要求：\n" + "\n".join(f"- {item}" for item in requirements))
    if assistant_parts:
        sections.append("已有上下文：\n" + "\n\n".join(assistant_parts))
    return "\n".join(sections).strip()

def _browser_prompt(
    messages: list[dict[str, str]],
    *,
    response_format: bool,
    allow_external_read: bool = False,
    allow_generated_artifacts: bool = False,
) -> str:
    return _conversation_prompt(
        messages,
        response_format=response_format,
        allow_external_read=allow_external_read,
        allow_generated_artifacts=allow_generated_artifacts,
        channel="browser",
    )

__all__ = ["_browser_prompt", "_cli_prompt", "_conversation_prompt"]

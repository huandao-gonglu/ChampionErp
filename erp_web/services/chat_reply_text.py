"""主对话可见正文的边界，文本中的标记永远不执行。"""
import re


def visible_reply(text: str) -> str:
    """与网页一致，只隐藏开头的思考标签；不改写原生历史。"""
    while text.lstrip().startswith("<think>"):
        text = text.lstrip()
        end = text.find("</think>", len("<think>"))
        if end < 0:
            return ""
        text = text[end + len("</think>"):]
    return text.strip()


def is_tool_call_text(text: str) -> bool:
    """识别正文开头的工具协议封装，普通说明和围栏内的代码示例可原样显示。"""
    return re.match(r"<run_code>\s*<code>|<[｜|]{1,2}DSML[｜|]{1,2}\s*(?:function_calls|invoke)\b",
                    visible_reply(text), re.IGNORECASE) is not None


__all__ = ["is_tool_call_text", "visible_reply"]

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


def has_unclosed_think(text: str) -> bool:
    """检查正文开头是否存在未闭合的 <think> 标签。"""
    rest = text
    while rest.lstrip().startswith("<think>"):
        rest = rest.lstrip()
        end = rest.find("</think>", len("<think>"))
        if end < 0:
            return True
        rest = rest[end + len("</think>"):]
    return False


_TOOL_CALL_PREFIX_RE = re.compile(
    r"<run_code>\s*<code>|<[｜|]{1,2}DSML[｜|]{1,2}\s*(?:function_calls|calls|invoke)\b",
    re.IGNORECASE,
)
_DSML_MARKUP_RE = re.compile(
    r"</?[｜|]{1,2}DSML[｜|]{1,2}\s*(?:function_calls|calls|invoke|parameter)\b",
    re.IGNORECASE,
)


def is_tool_call_text(text: str) -> bool:
    """识别工具协议封装或残留的 DSML 标记（含未闭合 <think> 后的泄露），普通说明和围栏内的代码示例可原样显示。"""
    reply = visible_reply(text)
    return _TOOL_CALL_PREFIX_RE.match(reply) is not None or _DSML_MARKUP_RE.search(text) is not None


__all__ = ["has_unclosed_think", "is_tool_call_text", "visible_reply"]

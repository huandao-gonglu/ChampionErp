"""同一可见对话的执行范围；并发回复各自持有原生工具和审批历史。"""
import re
from uuid import NAMESPACE_URL, uuid5

_GLOBAL = "conversation_global_chat_"
_REPLY = "chat_reply_"
_REPLY_ID = re.compile(r"chat_reply_([0-9a-f]{32})_[0-9a-f]{32}")


def is_global_id(value: str) -> bool:
    return bool(re.fullmatch(r"conversation_global_chat_[0-9a-f]{32}", value))


def reply_root(value: str) -> str | None:
    match = _REPLY_ID.fullmatch(value)
    return _GLOBAL + match[1] if match else None


def reply_prefix(root_id: str) -> str:
    if not is_global_id(root_id):
        raise ValueError("即时回复需要全局对话。")
    return _REPLY + root_id.removeprefix(_GLOBAL) + "_"


def parallel_run_id(conversation_id: str, message_id: str) -> str:
    return reply_prefix(reply_root(conversation_id) or conversation_id) + uuid5(
        NAMESPACE_URL, f"{conversation_id}/{message_id}"
    ).hex

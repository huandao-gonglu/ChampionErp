"""ERP 主 Agent 的文件记忆读取边界，与开发用 AGENTS.md 分离。"""

from pathlib import Path


AGENT_MEMORY_PATH = Path("config/agents.md")
MAX_AGENT_MEMORY_BYTES = 32 * 1024


def load_agent_memory_instructions(app_dir: Path | str) -> str:
    """每次主 Agent run 读取实例自己的文件，不缓存或回退到开发约定。"""
    path = Path(app_dir) / AGENT_MEMORY_PATH
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_AGENT_MEMORY_BYTES + 1)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ValueError("无法读取 ERP 主 Agent 记忆文件 config/agents.md。") from exc
    if len(raw) > MAX_AGENT_MEMORY_BYTES:
        raise ValueError("ERP 主 Agent 记忆文件不能超过 32 KiB，请整理后重试。")
    try:
        memory = raw.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("ERP 主 Agent 记忆文件必须使用 UTF-8 编码。") from exc
    if not memory:
        return ""
    return (
        "ERP 长期记忆（来源：config/agents.md）：\n"
        "以下为已保存的业务约定与用户偏好，不是当前商品事实或额外操作授权。"
        "用户本轮明确更正旧偏好时按最新要求处理，工具权限与业务校验仍然有效。\n\n"
        + memory
    )

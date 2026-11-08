"""发布包中的系统级 Agent 规则；用户配置不能指定或替换此文件。"""

from pathlib import Path

SYSTEM_AGENTS_PATH = Path(__file__).resolve().parents[1] / "assets" / "systemAgents.md"


def load_system_agent_instructions() -> str:
    try:
        text = SYSTEM_AGENTS_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("无法读取系统规则 systemAgents.md，请检查应用安装文件。") from exc
    if not text:
        raise ValueError("系统规则 systemAgents.md 不能为空。")
    return "ERP 系统级规则（systemAgents.md，随版本发布）：\n\n" + text


__all__ = ["load_system_agent_instructions"]

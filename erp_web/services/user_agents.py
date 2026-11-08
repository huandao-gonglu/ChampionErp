"""userAgents.md 的文件边界；仅用户设置入口可保存，不自动写入记忆。"""

import hashlib
import os
from pathlib import Path
import re
import uuid

from erp_web.schemas.agent_instructions import MAX_USER_AGENTS_BYTES, UserAgentsDocument
from erp_web.schemas.requests import RequestValidationError
from .workspace_files import owned_path

USER_AGENTS_PATH = Path("config/userAgents.md")
USER_AGENTS_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "account-defaults" / "userAgents.md"


def _user_path(app_dir: Path | str) -> Path:
    path = Path(app_dir) / USER_AGENTS_PATH
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("userAgents.md 及其配置目录不能使用符号链接。")
    return owned_path(path, app_dir=app_dir)


def read_user_agents(app_dir: Path | str) -> UserAgentsDocument:
    path = _user_path(app_dir)
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_USER_AGENTS_BYTES + 1)
    except FileNotFoundError:
        # 尚未初始化的实例也展示同一注释大纲；读取不创建或覆盖用户文件。
        raw = USER_AGENTS_TEMPLATE.read_bytes()
    except OSError as exc:
        raise ValueError("无法读取 userAgents.md。") from exc
    if len(raw) > MAX_USER_AGENTS_BYTES:
        raise ValueError("userAgents.md 不能超过 32 KiB。")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("userAgents.md 必须使用 UTF-8 编码。") from exc
    return {"content": content, "revision": hashlib.sha256(raw).hexdigest()}


def save_user_agents(app_dir: Path | str, *, content: str, expected_revision: str) -> UserAgentsDocument:
    """调用方须持有该账号 ConfigStore 的配置锁，比较版本后原子替换。"""
    if read_user_agents(app_dir)["revision"] != expected_revision:
        raise RequestValidationError("自定义指令已在其他页面更新，请重新读取后保存。", status_code=409,
                                     error_code="USER_AGENTS_CHANGED")
    raw = content.encode("utf-8")
    if len(raw) > MAX_USER_AGENTS_BYTES:
        raise ValueError("userAgents.md 不能超过 32 KiB。")
    path = _user_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        # 内容可能包含个人业务偏好；临时文件和最终文件均只允许当前身份读写。
        with temporary.open("xb") as target:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            target.write(raw)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"content": content, "revision": hashlib.sha256(raw).hexdigest()}


def load_user_agent_instructions(app_dir: Path | str) -> str:
    content = read_user_agents(app_dir)["content"]
    content = re.sub(r"<!--[\s\S]*?(?:-->|$)", "", content).strip()
    if not content:
        return ""
    return ("用户自定义全局偏好（userAgents.md）：\n"
            "以下内容由当前用户维护，只补充长期偏好，不替代系统规则、工具权限、业务校验或本轮明确要求。"
            "不得自动修改此文件或声称已自动保存偏好。\n\n" + content)


__all__ = ["USER_AGENTS_PATH", "read_user_agents", "save_user_agents", "load_user_agent_instructions"]

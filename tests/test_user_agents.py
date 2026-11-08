"""唯一用户指令文件的保存、并发保护与数据隔离。"""

from pathlib import Path
import re

import pytest

from erp_web.context import get_context
from erp_web.schemas.requests import RequestValidationError, validate_request_payload
from erp_web.services.user_agents import read_user_agents, load_user_agent_instructions


def test_initial_outline_is_comments_only_and_read_does_not_write(app_dir):
    document = get_context().config.user_agents()
    assert "交流偏好" in document["content"]
    assert re.sub(r"<!--[\s\S]*?-->", "", document["content"]).strip() == ""
    assert load_user_agent_instructions(app_dir) == ""
    assert not (app_dir / "config/userAgents.md").exists()


def test_save_clear_and_optimistic_concurrency_preserve_exact_text(app_dir):
    config = get_context().config
    first = config.user_agents()
    content = "<!-- 仅供编辑参考 -->\n\n## 回复偏好\n请先给结果。\n"
    saved = config.save_user_agents({"content": content, "expected_revision": first["revision"]})
    assert saved == read_user_agents(app_dir)
    path = app_dir / "config/userAgents.md"
    assert path.read_bytes() == content.encode()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "仅供编辑参考" not in load_user_agent_instructions(app_dir)
    assert "请先给结果" in load_user_agent_instructions(app_dir)
    with pytest.raises(RequestValidationError) as error:
        config.save_user_agents({"content": "旧页面覆盖", "expected_revision": first["revision"]})
    assert error.value.status_code == 409
    assert path.read_text() == content
    payload = validate_request_payload({"content": "", "expected_revision": saved["revision"]},
                                       endpoint="/api/system-settings/user-agents")
    config.save_user_agents(payload)
    assert path.read_bytes() == b"" and load_user_agent_instructions(app_dir) == ""


def test_invalid_content_or_arbitrary_paths_do_not_replace_user_preferences(app_dir):
    config = get_context().config
    initial = config.user_agents()
    saved = config.save_user_agents({"content": "保留这段内容", "expected_revision": initial["revision"]})
    for extra in ({"content": "汉" * 12000}, {"path": "/tmp/systemAgents.md"}):
        with pytest.raises(ValueError):
            config.save_user_agents({"content": "不应保存", "expected_revision": saved["revision"], **extra})
    assert config.user_agents() == saved
    assert not list((app_dir / "config").glob(".userAgents.md.*.tmp"))


def test_failed_atomic_replace_preserves_original_file(monkeypatch, app_dir):
    config = get_context().config
    initial = config.user_agents()
    saved = config.save_user_agents({"content": "旧内容", "expected_revision": initial["revision"]})
    def fail(*args):
        raise OSError("模拟替换失败")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="模拟替换失败"):
        config.save_user_agents({"content": "新内容", "expected_revision": saved["revision"]})
    assert config.user_agents() == saved
    assert not list((app_dir / "config").glob(".userAgents.md.*.tmp"))


def test_user_agents_symlink_cannot_read_or_overwrite_another_file(app_dir, tmp_path):
    other = tmp_path / "another-account.md"
    other.write_text("其他账号资料")
    path = app_dir / "config/userAgents.md"
    path.parent.mkdir(exist_ok=True)
    path.symlink_to(other)
    with pytest.raises(ValueError, match="符号链接"):
        read_user_agents(app_dir)
    assert other.read_text() == "其他账号资料"

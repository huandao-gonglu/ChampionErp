from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import urllib.request

import pytest

from erp_web.desktop import prepare_environment


def test_desktop_environment_replaces_development_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setenv("ERP_APP_DIR", "/开发目录")
    monkeypatch.setenv("ERP_PORT", "5050")
    monkeypatch.setenv("ERP_LOG_FILE", "/开发目录/backend.log")
    monkeypatch.setenv("ERP_BROWSER_PROFILE_DIR", "/开发目录/browser")
    monkeypatch.setenv("CHAMPION_ERP_BACKEND_URL", "http://127.0.0.1:5050")
    prepare_environment(tmp_path)
    assert os.environ["ERP_APP_DIR"] == str(tmp_path)
    assert os.environ["ERP_PORT"] == "0"
    assert os.environ["ERP_LOG_FILE"].startswith(str(tmp_path))
    assert os.environ["ERP_BROWSER_DEBUG_PORT"] != "9222"
    assert "CHAMPION_ERP_BACKEND_URL" not in os.environ


def test_managed_browser_does_not_attach_to_another_environment(monkeypatch, tmp_path):
    from erp_web.services import browser_debug_service as browsers

    monkeypatch.setattr(browsers, "browser_debug_connected", lambda port: True)
    monkeypatch.setattr(browsers, "_browser_version", lambda port: {"webSocketDebuggerUrl": "ws://development"})
    with pytest.raises(RuntimeError, match="其他会话"):
        browsers.ensure_browser_debug_session(19222, tmp_path)
    (tmp_path / ".erp-browser-session.json").write_text(json.dumps({"websocket": "ws://development"}))
    browsers.ensure_browser_debug_session(19222, tmp_path)


def test_backend_uses_ephemeral_port_and_stops_on_parent_eof(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "standalone"
    with (tmp_path / "stderr.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, "-m", "erp_web.desktop", "--data-dir", str(data_dir)],
            cwd=root, env={**os.environ, "ERP_PORT": "5050", "ERP_APP_DIR": str(tmp_path / "wrong")},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(40), "桌面后端未报告就绪"
                line = process.stdout.readline()
            if not line.startswith("CHAMPION_ERP_READY "):
                log.seek(0)
                pytest.fail(log.read()[-5000:])
            message = json.loads(line.split(" ", 1)[1])
            assert message["pid"] == process.pid
            assert ":5050/" not in message["url"]
            with urllib.request.urlopen(message["url"] + "api/state", timeout=15) as response:
                assert json.load(response)["ok"]
            assert (data_dir / "erp.sqlite3").exists()
            assert not (tmp_path / "wrong").exists()
            process.stdin.close()
            assert process.wait(timeout=20) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

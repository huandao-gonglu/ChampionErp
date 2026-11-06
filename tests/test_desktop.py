import json
import pytest

def test_managed_browser_does_not_attach_to_another_environment(monkeypatch, tmp_path):
    from desktop_runtime import browser_debug_service as browsers

    monkeypatch.setattr(browsers, "browser_debug_connected", lambda port: True)
    monkeypatch.setattr(browsers, "_browser_version", lambda port: {"webSocketDebuggerUrl": "ws://development"})
    with pytest.raises(RuntimeError, match="其他会话"):
        browsers.ensure_browser_debug_session(19222, tmp_path)
    (tmp_path / ".erp-browser-session.json").write_text(json.dumps({"websocket": "ws://development"}))
    browsers.ensure_browser_debug_session(19222, tmp_path)

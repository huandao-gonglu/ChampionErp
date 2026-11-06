"""Browser AI 的后端适配器；所有本机执行经当前桌面连接。"""
from erp_shared.browser_ai import (
    BrowserAiRunResult, normalize_browser_provider, normalize_browser_mode,
    browser_ai_url,
)
from .desktop_connection_service import call_desktop


def run_browser_ai_chat(app_dir, model, prompt, *, timeout=180):
    return BrowserAiRunResult(**call_desktop('browser.ai.chat', {'model': model, 'prompt': prompt}, timeout=timeout))


def open_browser_ai_page(app_dir, model, timeout=30):
    return BrowserAiRunResult(**call_desktop('browser.ai.open', {'model': model}, timeout=timeout))


__all__ = ['BrowserAiRunResult', 'normalize_browser_provider', 'normalize_browser_mode', 'browser_ai_url', 'run_browser_ai_chat', 'open_browser_ai_page']

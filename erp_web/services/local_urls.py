"""服务端口与资产文件 URL 的纯基础工具。"""
import urllib.parse
from pathlib import Path

def file_url(path: Path) -> str:
    return f"/file?path={urllib.parse.quote(str(path), safe='')}"

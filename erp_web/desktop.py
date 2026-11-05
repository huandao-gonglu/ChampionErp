"""桌面后端入口：先隔离目录和端口，再导入并运行原有 HTTP 服务。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import sys
import threading


def prepare_environment(data_dir: Path) -> None:
    """忽略启动终端的开发变量；桌面配置只从自己的目录加载。"""
    for key in list(os.environ):
        if key.startswith(("ERP_", "CHAMPION_ERP_", "VITE_")):
            del os.environ[key]
    os.environ.update({
        "ERP_APP_DIR": str(data_dir),
        "ERP_PORT": "0",
        "ERP_NO_BROWSER": "1",
        "ERP_BROWSER_PROFILE_DIR": str(data_dir / "browser_profile" / "debug"),
        "ERP_BROWSER_DEBUG_PORT": "19222",
        "ERP_AI_BROWSER_DEBUG_PORT": "19333",
        "ERP_LOG_FILE": str(data_dir / "data" / "logs" / "backend.log"),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="Champion ERP 独立桌面后端")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    os.umask(0o077)
    prepare_environment(data_dir)

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundled_config = Path(__file__).resolve().parents[1] / "config"
    defaults = [bundled_config / "agents.md", *(bundled_config / "prompts").rglob("*.json"),
                *(bundled_config / "presets").rglob("*.json")]
    for source in defaults:
        if source.is_file():
            destination = data_dir / "config" / source.relative_to(bundled_config)
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(source, destination)
    os.chdir(data_dir)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())

    def watch_parent():
        # stdin 的管道由桌面主进程持有；退出或崩溃后 EOF 触发后端收尾。
        sys.stdin.buffer.read()
        stopped.set()

    threading.Thread(target=watch_parent, name="erp-desktop-parent", daemon=True).start()
    from erp_web.server import main as run_server

    def ready(url: str):
        print("CHAMPION_ERP_READY " + json.dumps({"url": url, "pid": os.getpid()}), flush=True)

    run_server(on_ready=ready, stop_event=stopped)


if __name__ == "__main__":
    main()

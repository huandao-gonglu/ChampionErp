"""用独立构建环境冻结独立本机执行器，不修改开发虚拟环境，不收集业务数据。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="champion-erp-build-") as temporary:
        work = Path(temporary)
        env_root = work / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(env_root)], check=True)
        python = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                        "-r", str(ROOT / "requirements-desktop.txt"),
                        "pyinstaller==6.22.3", "pyinstaller-hooks-contrib==2026.9"], check=True)
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("ERP_", "VITE_", "CHAMPION_ERP_"))}
        subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--distpath", str(ROOT / "front" / ".desktop-runtime"),
                        "--workpath", str(work / "build"), str(ROOT / "scripts" / "desktop_runtime.spec")],
                       cwd=ROOT, env=environment, check=True)
    print("本机执行器已构建：front/.desktop-runtime/champion-erp-runtime")


if __name__ == "__main__":
    main()

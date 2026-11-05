"""用独立构建环境冻结当前后端，不修改开发虚拟环境，不收集业务数据。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    runtime_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not runtime_python.is_file():
        raise SystemExit("请先安装项目 .venv 和 requirements.txt 对应的后端依赖。")
    with tempfile.TemporaryDirectory(prefix="champion-erp-build-") as temporary:
        work = Path(temporary)
        env_root = work / "venv"
        subprocess.run([str(runtime_python), "-m", "venv", str(env_root)], check=True)
        python = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        paths_code = "import json,sysconfig;print(json.dumps([sysconfig.get_path('purelib'),sysconfig.get_path('platlib')]))"
        source_paths = json.loads(subprocess.check_output([str(runtime_python), "-c", paths_code], text=True))
        build_paths = json.loads(subprocess.check_output([str(python), "-c", paths_code], text=True))
        Path(build_paths[0], "erp-runtime.pth").write_text("\n".join(dict.fromkeys(source_paths)) + "\n")
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                        "pyinstaller==6.22.3", "pyinstaller-hooks-contrib==2026.9"], check=True)
        environment = {**os.environ, "ERP_APP_DIR": str(work / "analysis-data"), "ERP_NO_BROWSER": "1"}
        subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--distpath", str(ROOT / "front" / ".desktop-backend"),
                        "--workpath", str(work / "build"), str(ROOT / "scripts" / "desktop_backend.spec")],
                       cwd=ROOT, env=environment, check=True)
    print("桌面后端已构建：front/.desktop-backend/champion-erp-backend")


if __name__ == "__main__":
    main()

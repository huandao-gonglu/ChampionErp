"""用独立构建环境冻结独立本机执行器，不修改开发虚拟环境，不收集业务数据。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_NAME = "champion-erp-runtime.exe" if os.name == "nt" else "champion-erp-runtime"
CACHE_KEEP = 2


def _input_digest(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(f"{sys.implementation.cache_tag}:{sys.version_info[:3]}:{sysconfig.get_platform()}\n".encode())
    files: list[Path] = []
    for folder_name in ("desktop_runtime", "erp_shared"):
        folder = root / folder_name
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    files.append(path)
    for rel in (
        "scripts/desktop_runtime_entry.py",
        "scripts/desktop_runtime.spec",
        "scripts/build_desktop_runtime.py",
        "requirements-desktop.txt",
        "requirements-desktop-build.txt",
        "LICENSE",
    ):
        path = root / rel
        if path.is_file():
            files.append(path)
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:24]


def _copy_bundle(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".runtime-stage-") as temporary:
        staged = Path(temporary) / target.name
        shutil.copytree(source, staged, symlinks=True)
        if target.exists():
            shutil.rmtree(target)
        staged.replace(target)


def _prune_cache(cache_root: Path, keep_key: str) -> None:
    entries = [
        path for path in cache_root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != keep_key
    ]
    entries.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in entries[max(0, CACHE_KEEP - 1):]:
        shutil.rmtree(stale, ignore_errors=True)


def main() -> None:
    target = ROOT / "front" / ".desktop-runtime" / "champion-erp-runtime"
    cache_root = Path.home() / ".cache" / "champion-erp" / "desktop-runtime"
    cache_key = _input_digest(ROOT)
    cached = cache_root / cache_key / "champion-erp-runtime"
    if (cached / EXECUTABLE_NAME).is_file():
        _copy_bundle(cached, target)
        cached.parent.touch()
        print(f"复用本机执行器缓存：{cache_key}", flush=True)
        return

    with tempfile.TemporaryDirectory(prefix="champion-erp-build-") as temporary:
        work = Path(temporary)
        env_root = work / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(env_root)], check=True)
        python = env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), str(ROOT / "scripts/install_release_dependencies.py"),
                        str(ROOT / "requirements-desktop-build.txt")], check=True)
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("ERP_", "VITE_", "CHAMPION_ERP_"))}
        subprocess.run([str(python), "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--distpath", str(ROOT / "front" / ".desktop-runtime"),
                        "--workpath", str(work / "build"), str(ROOT / "scripts" / "desktop_runtime.spec")],
                       cwd=ROOT, env=environment, check=True)

    if (target / EXECUTABLE_NAME).is_file():
        cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache_root, prefix=".staging-") as temporary:
            staged = Path(temporary) / "bundle"
            shutil.copytree(target, staged / "champion-erp-runtime", symlinks=True)
            entry = cache_root / cache_key
            if not entry.exists():
                try:
                    staged.replace(entry)
                except OSError:
                    pass
        _prune_cache(cache_root, cache_key)
    print("本机执行器已构建：front/.desktop-runtime/champion-erp-runtime")


if __name__ == "__main__":
    main()

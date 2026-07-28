#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON_TIMEOUT_SECONDS = 30 * 60
FRONTEND_TIMEOUT_SECONDS = 15 * 60


def _run(label: str, command: list[str], *, timeout: int) -> bool:
    print(f"\n[{label}] {' '.join(command)}", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[{label}] 超时（{timeout} 秒）", flush=True)
        return False
    if result.returncode == 0:
        print(f"[{label}] 通过", flush=True)
        return True
    print(f"[{label}] 失败（退出码 {result.returncode}）", flush=True)
    return False


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行重构验收门禁（后端、前端类型、前端测试和构建）。"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--backend-only",
        action="store_true",
        help="只运行 Python 测试。",
    )
    mode.add_argument(
        "--frontend-only",
        action="store_true",
        help="只运行前端 typecheck、test:run 和 build。",
    )
    parser.add_argument(
        "--acceptance-only",
        action="store_true",
        help="Python 部分只运行 tests/refactor_acceptance；默认运行 tests 全量。",
    )
    args = parser.parse_args()
    if args.frontend_only and args.acceptance_only:
        parser.error(
            "--acceptance-only 只影响 Python 测试，不能与 --frontend-only 同用"
        )
    return args


def main() -> int:
    args = _arguments()
    results: list[bool] = []

    if not args.frontend_only:
        if importlib.util.find_spec("pytest") is None:
            print(
                f"当前解释器 {sys.executable} 未安装 pytest。\n"
                f"请先运行：{sys.executable} -m pip install -r "
                f"{ROOT / 'requirements-dev.txt'}",
                file=sys.stderr,
            )
            results.append(False)
        else:
            pytest_target = (
                "tests/refactor_acceptance"
                if args.acceptance_only
                else "tests"
            )
            results.append(
                _run(
                    "Python",
                    [sys.executable, "-m", "pytest", pytest_target, "-q"],
                    timeout=PYTHON_TIMEOUT_SECONDS,
                )
            )

    if not args.backend_only:
        pnpm = shutil.which("pnpm")
        if not pnpm:
            print(
                "未找到 pnpm；无法执行完整前端验收。"
                "可安装 pnpm，或显式使用 --backend-only。",
                file=sys.stderr,
            )
            results.append(False)
        else:
            checks = [
                ("前端类型", [pnpm, "--dir", "front", "run", "typecheck"]),
                ("前端测试", [pnpm, "--dir", "front", "run", "test:run"]),
                ("前端构建", [pnpm, "--dir", "front", "run", "build"]),
            ]
            results.extend(
                _run(
                    label,
                    command,
                    timeout=FRONTEND_TIMEOUT_SECONDS,
                )
                for label, command in checks
            )

    if all(results):
        print("\n所选重构验收门禁全部通过。", flush=True)
        return 0
    failed = sum(not result for result in results)
    print(f"\n重构验收未通过：{failed}/{len(results)} 个门禁失败。", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

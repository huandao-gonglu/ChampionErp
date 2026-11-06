"""Codex CLI 的本机执行；仅提供已有固定 exec 行为。"""
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import os
import signal
from pathlib import Path
from typing import Any
from erp_shared.ai_parsing import _sanitize_cli_error
from .operation import check_cancellation, remaining_seconds

def _cli_command_parts(command: str) -> list[str]:
    parts = shlex.split(str(command or "").strip())
    if not parts:
        raise RuntimeError("请先填写 CLI 命令路径。")
    return parts

def _codex_cli_args(app_dir: Path | str, model: dict[str, Any], output_path: str) -> list[str]:
    command = str(model.get("command") or "codex")
    args = _cli_command_parts(command)
    sandbox = str(model.get("sandbox") or "read-only").strip() or "read-only"
    profile = str(model.get("profile") or "").strip()
    model_name = str(model.get("model") or "")
    args.extend(["exec", "--color", "never", "--ephemeral", "--skip-git-repo-check", "-C", str(app_dir), "--sandbox", sandbox])
    if profile:
        args.extend(["-p", profile])
    if model_name:
        args.extend(["-m", model_name])
    args.extend(["-o", output_path, "-"])
    return args

def _run_codex_cli_text(
    app_dir: Path | str,
    model: dict[str, Any],
    prompt: str,
    timeout: int,
) -> str:
    command = str(model.get("command") or "codex")
    executable = _cli_command_parts(command)[0]
    if not shutil.which(executable):
        raise RuntimeError(f"未找到本地 CLI 命令：{executable}。请先安装，或填写完整命令路径。")
    with tempfile.TemporaryDirectory(prefix="champion_erp_codex_") as directory:
        output_path = Path(directory) / "result.txt"
        args = _codex_cli_args(app_dir, model, str(output_path))
        completed = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, cwd=str(app_dir), start_new_session=os.name != "nt")
        deadline = time.monotonic() + timeout
        try:
            first = True
            while True:
                check_cancellation()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Codex CLI 调用超时（{timeout} 秒）；结果需要核对。")
                try:
                    stdout, stderr = completed.communicate(input=prompt if first else None, timeout=remaining_seconds(min(0.5, remaining)))
                    break
                except subprocess.TimeoutExpired:
                    first = False
        except BaseException:
            if os.name != "nt":
                os.killpg(completed.pid, signal.SIGTERM)
            else:
                completed.terminate()
            try:
                completed.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(completed.pid, signal.SIGKILL)
                else:
                    completed.kill()
                completed.wait()
            raise
        if completed.returncode != 0:
            detail = _sanitize_cli_error(stderr or stdout)
            model_name = str(model.get("model") or "")
            if model_name and re.search(r"model .*not supported|model metadata .*not found", detail, flags=re.IGNORECASE):
                raise RuntimeError(
                    f"Codex CLI 模型 {model_name} 不可用。请清空 CLI 模型字段使用本机 Codex 默认模型，"
                    "或填写 Codex CLI 支持的模型名；不要沿用 DeepSeek/OpenAI-Compatible 的 API 模型名。"
                )
            hint = "请在终端运行 codex login 或 codex doctor 检查本机 Codex 状态。"
            raise RuntimeError(f"Codex CLI 调用失败：{detail or '命令返回非 0 状态'}。{hint}")
        final_text = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
    return final_text or str(stdout or "").strip()


from __future__ import annotations

import ast
import base64
import inspect
import json
import os
from dataclasses import replace
from pathlib import Path
from unittest import mock

from erp_web.context import AppContext, AppPaths, get_context, set_context
from erp_web.db import ErpDatabase
from erp_web.runtime_units import (
    collect_helpers,
    copy_generation,
    image_pool,
    image_pool_core,
    publish_logs_runtime,
    publish_mercadolibre,
    source_collect_browser,
    source_collect_workflows,
)
from tests.runtime_test_utils import temp_app_context


def test_runtime_units_do_not_import_path_snapshots() -> None:
    erp_web_dir = Path(__file__).resolve().parents[1] / "erp_web"
    assert not (erp_web_dir / "runtime_units" / "runtime_common.py").exists()
    offenders: list[str] = []
    for path in sorted(erp_web_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                targets = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            if any(
                target == "runtime_common"
                or target.endswith(".runtime_common")
                for target in targets
            ):
                offenders.append(f"{path.relative_to(erp_web_dir)}:{node.lineno}")
    assert not offenders, f"生产模块仍导入 runtime_common 路径快照：{offenders}"


def test_runtime_paths_follow_context_rebinding_after_module_import(tmp_path: Path) -> None:
    def capture(root: Path) -> tuple[dict[str, Path], dict]:
        with temp_app_context(root) as context:
            context.paths.chatgpt_dir.mkdir(parents=True, exist_ok=True)
            generated = context.paths.chatgpt_dir / "generated.png"
            generated.write_bytes(b"image")
            values = {
                "collect": collect_helpers.collect_debug_path("context", ".txt"),
                "upload": image_pool._uploaded_image_path("context.png", ".png"),
                "relative": image_pool_core._resolve_local_image_ref("images/context.png"),
                "artifact": publish_logs_runtime._publish_artifact_paths("mercadolibre")[0],
                "last_payload": publish_mercadolibre._last_mercadolibre_payload_path(),
            }
            generated_images = image_pool_core.current_generated_images()
            assert copy_generation.list_presets()
            return values, generated_images

    first, first_generated = capture(tmp_path / "first")
    second, second_generated = capture(tmp_path / "second")

    assert first["collect"].parent == tmp_path / "first/data/cache/collect_debug"
    assert first["upload"].parent == tmp_path / "first/data/images/uploads"
    assert first["relative"] == tmp_path / "first/images/context.png"
    assert first["artifact"].is_relative_to(tmp_path / "first/data/logs")
    assert first["last_payload"] == tmp_path / "first/data/logs/last_mercadolibre_payload.json"
    assert Path(first_generated[0]["path"]).is_relative_to(tmp_path / "first")

    assert second["collect"].parent == tmp_path / "second/data/cache/collect_debug"
    assert second["upload"].parent == tmp_path / "second/data/images/uploads"
    assert second["relative"] == tmp_path / "second/images/context.png"
    assert second["artifact"].is_relative_to(tmp_path / "second/data/logs")
    assert second["last_payload"] == tmp_path / "second/data/logs/last_mercadolibre_payload.json"
    assert Path(second_generated[0]["path"]).is_relative_to(tmp_path / "second")


def test_browser_port_defaults_are_resolved_at_call_time(tmp_path: Path) -> None:
    assert inspect.signature(source_collect_browser.browser_debug_status).parameters["port"].default is None
    assert inspect.signature(source_collect_workflows.collect_from_browser_tab).parameters["port"].default is None

    previous = get_context()
    paths = replace(
        AppPaths.from_app_dir(tmp_path / "browser-port"),
        browser_debug_port=19_822,
    )
    context = AppContext(paths, ErpDatabase(paths.db_path))
    set_context(context)
    try:
        status = source_collect_browser.browser_debug_status(tabs_override=[])
    finally:
        set_context(previous)

    assert status["port"] == 19_822


def test_publish_artifact_names_are_unique_and_private_in_same_second(
    tmp_path: Path,
) -> None:
    with temp_app_context(tmp_path / "artifacts") as context:
        with (
            mock.patch.object(
                publish_logs_runtime.time,
                "strftime",
                return_value="20260729-230000",
            ),
            mock.patch.object(
                publish_logs_runtime.time,
                "time_ns",
                return_value=123456789,
            ),
        ):
            first = publish_logs_runtime._write_publish_artifacts(
                "mercadolibre",
                {"request": 1},
                {"response": 1},
            )
            second = publish_logs_runtime._write_publish_artifacts(
                "mercadolibre",
                {"request": 2},
                {"response": 2},
            )

        paths = [Path(path) for path in (*first, *second)]
        assert len(set(paths)) == 4
        assert all(
            path.is_relative_to(context.paths.output_dir)
            for path in paths
        )
        assert json.loads(paths[0].read_text(encoding="utf-8")) == {
            "request": 1
        }
        assert json.loads(paths[2].read_text(encoding="utf-8")) == {
            "request": 2
        }
        if os.name != "nt":
            assert all(
                path.stat().st_mode & 0o777 == 0o600
                for path in paths
            )


def test_collect_debug_artifacts_are_created_private(
    tmp_path: Path,
) -> None:
    with temp_app_context(tmp_path / "collect-debug") as context:
        artifacts = collect_helpers.save_collect_snapshot_artifacts(
            "1688",
            "https://detail.1688.com/offer/1.html",
            html="<html>secret snapshot</html>",
            screenshot_base64=base64.b64encode(
                b"fake-png"
            ).decode("ascii"),
        )
        text_path = Path(
            collect_helpers.write_collect_debug_text(
                "1688",
                "private debug text",
            )
        )
        paths = [
            Path(artifacts["html_snapshot_path"]),
            Path(artifacts["screenshot_path"]),
            text_path,
        ]

        assert all(path.is_file() for path in paths)
        assert all(
            path.parent == context.paths.collect_debug_dir
            for path in paths
        )
        if os.name != "nt":
            assert (
                context.paths.collect_debug_dir.stat().st_mode
                & 0o777
                == 0o700
            )
            assert all(
                path.stat().st_mode & 0o777 == 0o600
                for path in paths
            )

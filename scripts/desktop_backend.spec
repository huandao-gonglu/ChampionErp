# -*- mode: python ; coding: utf-8 -*-
"""只打包代码、依赖与公开默认配置，禁止收集开发目录的运行数据。"""
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [
    (str(root / "config" / "agents.md"), "config"),
    (str(root / "config" / "prompts"), "config/prompts"),
    (str(root / "config" / "presets"), "config/presets"),
    (str(root / "LICENSE"), "."),
    (str(Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"), "licenses/python"),
]
datas += collect_data_files("erp_web", includes=["assets/**/*", "international_shipping/*.json"])
for package in ("pydantic_ai", "pydantic_ai_harness", "pydantic_monty", "botocore", "certifi", "genai_prices", "wechatbot"):
    datas += collect_data_files(package)
# 原生插件及运行时版本检测使用 importlib.metadata；依赖版本以实际构建环境为准。
for name in ("pydantic-ai-slim", "pydantic-ai-harness", "pydantic-monty", "openai", "httpx2",
             "opentelemetry-sdk", "pillow", "requests", "python-dotenv", "snowballstemmer", "botocore",
             "wechatbot-sdk", "qrcode"):
    datas += copy_metadata(name, recursive=True)

a = Analysis(
    [str(root / "scripts" / "desktop_backend_entry.py")],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=collect_submodules("erp_web") + ["opentelemetry.sdk.trace", "opentelemetry.sdk.metrics"],
    excludes=["pytest", "tkinter", "IPython", "matplotlib"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="champion-erp-backend", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="champion-erp-backend")

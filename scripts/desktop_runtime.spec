# -*- mode: python ; coding: utf-8 -*-
"""只打包本机执行器及共享纯契约，不收集 ERP 后端或业务数据。"""
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules, copy_metadata
root = Path(SPECPATH).parent
python_license = next((candidate for candidate in (
    Path(sys.base_prefix) / 'LICENSE.txt',
    Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt',
) if candidate.is_file()), None)
if python_license is None:
    raise RuntimeError('构建环境缺少 Python 许可证文件，请补齐运行时分发资源。')
datas = [(str(root / 'LICENSE'), '.'), (str(python_license), 'licenses/python')]
for name in ('pydantic', 'pillow'):
    datas += copy_metadata(name, recursive=True)
a = Analysis([str(root / 'scripts' / 'desktop_runtime_entry.py')], pathex=[str(root)],
    datas=datas, hiddenimports=collect_submodules('desktop_runtime') + collect_submodules('erp_shared'),
    excludes=['erp_web', 'sqlite3', 'pydantic_ai', 'openai', 'botocore', 'wechatbot', 'pytest', 'tkinter'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='champion-erp-runtime', console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='champion-erp-runtime')

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import Counter

from .coverage_manifest import EXPECTED_REPORT_ISSUES, REPORT_ISSUE_TESTS
from .helpers import ROOT, dotted_name, function_definitions, parse_python


# 元验收：报告编号必须可追溯到真实可收集测试，且不得用 skip/xfail 隐藏。
def test_all_report_issues_have_live_acceptance_tests() -> None:
    report_path = ROOT / "docs/champion-Erp-设计审查报告.md"
    assert report_path.exists(), "缺少验收清单的来源设计审查报告"
    report = report_path.read_text(encoding="utf-8")
    required_headings = (
        "## 一、",
        "## 二、",
        "## 三、",
        "## 四、",
        "### A.",
        "### B.",
        "## 建议动刀顺序",
    )
    assert all(
        heading in report for heading in required_headings
    ), "设计审查报告章节结构变化，请同步 coverage manifest"
    sections = {
        "一": report.split("## 一、", 1)[1].split("## 二、", 1)[0],
        "二": report.split("## 二、", 1)[1].split("## 三、", 1)[0],
        "三": report.split("## 三、", 1)[1].split("## 四、", 1)[0],
        "四A": report.split("### A.", 1)[1].split("### B.", 1)[0],
        "四B": report.split("### B.", 1)[1].split("## 建议动刀顺序", 1)[0],
    }
    report_counts = {
        "一": len(re.findall(r"(?m)^\|\s*\d+\s*\|", sections["一"])),
        "二": len(re.findall(r"(?m)^\d+\.\s+\*\*", sections["二"])),
        "三": len(re.findall(r"(?m)^\d+\.\s+\*\*", sections["三"])),
        "四A": len(re.findall(r"(?m)^\d+\.\s+\*\*", sections["四A"])),
        "四B": len(re.findall(r"(?m)^\d+\.\s+\*\*", sections["四B"])),
    }
    assert report_counts == {"一": 12, "二": 10, "三": 12, "四A": 9, "四B": 5}
    assert set(REPORT_ISSUE_TESTS) == EXPECTED_REPORT_ISSUES
    assert all(REPORT_ISSUE_TESTS.values())

    acceptance_dir = ROOT / "tests/refactor_acceptance"
    discovered_nodes = [
        f"{path.name}::{definition.qualname}"
        for path in acceptance_dir.glob("test_*.py")
        if path.name != "test_coverage_manifest.py"
        for definition in function_definitions(path)
        if definition.qualname.split(".")[-1].startswith("test_")
    ]
    duplicates = [
        node_id
        for node_id, count in Counter(discovered_nodes).items()
        if count > 1
    ]
    assert not duplicates, f"存在被 Python 后定义覆盖的同名测试：{duplicates}"
    discovered = set(discovered_nodes)
    mapped = {
        node_id
        for node_ids in REPORT_ISSUE_TESTS.values()
        for node_id in node_ids
    }
    assert mapped == discovered, (
        f"映射到不存在测试：{sorted(mapped - discovered)}；"
        f"未登记测试：{sorted(discovered - mapped)}"
    )

    hidden: list[str] = []
    for path in acceptance_dir.glob("test_*.py"):
        for definition in function_definitions(path):
            if not definition.qualname.split(".")[-1].startswith("test_"):
                continue
            for decorator in definition.node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = dotted_name(target)
                if name.endswith((".skip", ".skipif", ".xfail")):
                    hidden.append(f"{path.name}::{definition.qualname} -> {name}")
    assert not hidden, f"验收测试禁止 skip/xfail：{hidden}"

    collection = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(acceptance_dir),
            "--collect-only",
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert collection.returncode == 0, collection.stdout + collection.stderr
    collected = {
        line.split("[", 1)[0].removeprefix(
            "tests/refactor_acceptance/"
        )
        for line in collection.stdout.splitlines()
        if line.startswith("tests/refactor_acceptance/")
        and "test_coverage_manifest.py::" not in line
    }
    assert mapped == collected, (
        f"映射但 pytest 未收集：{sorted(mapped - collected)}；"
        f"pytest 收集但未映射：{sorted(collected - mapped)}"
    )

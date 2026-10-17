"""仓库健康守卫：防"文档说谎"与"测试被静默吞掉"。

这两类问题都不报错、不崩、测试还全绿 —— 只能靠专门的守卫抓。
两个都是**踩过**的：

1. **文档指向不存在的东西**：`skeleton.py` 的注释曾写"用于
   `Skeleton.valence_errors`"，但那个方法从未实现。读者会以为功能已经有了。
2. **重复的类名静默吞掉测试**：`test_export.py` 里曾有两个 `FormatDocumentationTests`，
   后一个覆盖前一个，前一个里的 2 条测试**从未跑过**。其中一条其实是失败的
   （写死了公开仓库的版本号 `## [0.4.0]`，在私有仓库永远匹配不到）——
   因为被吞了，一直没人知道。
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _scan_files(*patterns):
    seen = []
    for pattern in patterns:
        seen.extend(sorted(REPO.glob(pattern)))
    return seen


class AdrReferenceTests(unittest.TestCase):
    """所有 `ADR-NNN` 引用都必须指向 `docs/DECISIONS.md` 里真的存在的条目。"""

    def test_no_dangling_adr_references(self):
        decisions = (REPO / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
        existing = set(re.findall(r"^#+\s*(ADR-\d+)", decisions, re.MULTILINE))
        self.assertGreater(len(existing), 5, "没扫到 ADR 条目，断言等于空转")

        dangling = {}
        for path in _scan_files("*.md", "docs/*.md", "src/**/*.py", "tests/*.py"):
            text = path.read_text(encoding="utf-8")
            for ref in set(re.findall(r"ADR-\d+", text)):
                if ref in existing:
                    continue
                dangling.setdefault(path.relative_to(REPO).as_posix(), []).append(ref)
        self.assertEqual(dangling, {},
                         "这些文件引用了不存在的 ADR：%s（要么写新 ADR，要么改文案）"
                         % dangling)


class TestDefinitionTests(unittest.TestCase):
    """一个测试文件里不许出现重复的类名 / 方法名 —— 后者会**静默覆盖**前者。

    Python 定义同名类/方法时后者直接顶掉前者，`unittest` 不会报任何错，
    受影响的测试从此不跑，而且退出码依然是 0。
    """

    def test_no_duplicate_class_names_in_a_module(self):
        problems = []
        for path in _scan_files("tests/test_*.py", "src/**/*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = [node.name for node in tree.body
                     if isinstance(node, ast.ClassDef)]
            duplicated = sorted({n for n in names if names.count(n) > 1})
            if duplicated:
                problems.append((path.relative_to(REPO).as_posix(), duplicated))
        self.assertEqual(problems, [],
                         "同名列出了两次，后一个会覆盖前一个（测试会静默不跑）：%s"
                         % problems)

    def test_no_duplicate_method_names_in_a_class(self):
        problems = []
        for path in _scan_files("tests/test_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                methods = [item.name for item in node.body
                           if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
                duplicated = sorted({m for m in methods if methods.count(m) > 1})
                if duplicated:
                    problems.append(("%s::%s" % (path.name, node.name), duplicated))
        self.assertEqual(problems, [],
                         "类里有重复方法名，后一个覆盖前一个：%s" % problems)

    def test_every_test_file_has_tests(self):
        """测试文件里至少得有一条 `test_*` 方法，否则它只是个摆设。"""
        empty = []
        for path in _scan_files("tests/test_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = False
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name.startswith("test_"):
                    found = True
                    break
            if not found:
                empty.append(path.name)
        self.assertEqual(empty, [], "这些测试文件里没有任何 test_ 方法：%s" % empty)


class RoadmapTests(unittest.TestCase):
    """ROADMAP 是给人看的承诺清单，不能烂掉。"""

    ROADMAP = REPO / "docs" / "ROADMAP.md"

    def test_roadmap_exists(self):
        self.assertTrue(self.ROADMAP.is_file(), "docs/ROADMAP.md 不见了")

    def test_roadmap_marks_completed_items_with_evidence(self):
        """已完成项必须带证据（commit hash 或代码位置），否则只是自我感觉。"""
        text = self.ROADMAP.read_text(encoding="utf-8")
        completed = text.split("## 已完成", 1)
        self.assertEqual(len(completed), 2, "ROADMAP 缺少「已完成」段落")
        section = completed[1].split("## 待办", 1)[0]
        # 证据 = 反引号包住的东西：commit hash、`file.py::symbol`、ADR 编号都算。
        # 不硬性要求 hash —— 私有仓库与公开仓库的历史不同，同一个 hash 不可能两边都有。
        evidence = re.findall(r"`[^`]+`", section)
        self.assertGreaterEqual(len(evidence), 4,
                                "已完成项的证据太少（应含 commit hash 或代码位置），"
                                "实际找到 %d 个" % len(evidence))

    def test_pending_items_use_checkboxes(self):
        text = self.ROADMAP.read_text(encoding="utf-8")
        self.assertIn("- [ ]", text, "待办项要用可勾选的 `- [ ]`")

    def test_roadmap_names_the_known_roadblock(self):
        """清单必须写清"明确不做"的部分，否则读者会期待不存在的东西。"""
        text = self.ROADMAP.read_text(encoding="utf-8")
        self.assertIn("明确**不做**", text)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()

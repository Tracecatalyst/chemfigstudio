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
import tempfile
import subprocess
import pathlib
import os
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


class PrivacyGuardTests(unittest.TestCase):
    """`scripts/check-privacy.sh` 必须真的能发现问题。

    只验证"脚本存在"是**假绿** —— 它完全可能永远返回 0。
    这里造一个含内网地址的临时仓库，断言脚本返回非零。
    """

    SCRIPT = REPO / "scripts" / "check-privacy.sh"

    def setUp(self):
        if not self.SCRIPT.is_file():
            self.skipTest("没有 check-privacy.sh")

    def test_script_exists_and_is_executable(self):
        self.assertTrue(self.SCRIPT.is_file())
        self.assertTrue(os.access(self.SCRIPT, os.X_OK), "脚本应当可执行")

    def _run_on(self, repo):
        return subprocess.run(["bash", str(self.SCRIPT), str(repo)],
                              capture_output=True, text=True, timeout=120)

    def test_flags_a_lan_ip_in_the_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            # 地址在运行时拼出来：**别把真实的私有地址写进仓库自己**，
            # 否则发布前自检会（正确地）把这个测试文件本身报成泄露。
            sample = ".".join(["192", "168", "7", "42"])
            (repo / "notes.txt").write_text(
                "调试时用的是 %s 这台机器\n" % sample, encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                            "commit", "-qm", "probe"], cwd=tmp, check=True)

            result = self._run_on(repo)
            self.assertNotEqual(result.returncode, 0,
                                "含内网 IP 却没报错：\n" + result.stdout)
            self.assertIn("内网 IP", result.stdout)

    def test_flags_a_home_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            # 同样在运行时拼接：仓库自己不该含"真实家目录"的样子
            sample_path = "/" + "Users" + "/" + "somebody" + "/project/x.png"
            (repo / "readme.md").write_text(
                "路径是 %s\n" % sample_path, encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                            "commit", "-qm", "probe"], cwd=tmp, check=True)

            result = self._run_on(repo)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("家目录", result.stdout)

    def test_allows_a_placeholder_path(self):
        """``/Users/you/...`` 这种占位符不该误报 —— 误报会让人把脚本关掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            (repo / "readme.md").write_text(
                'placeholder="/Users/you/Desktop"\n', encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                            "commit", "-qm", "probe"], cwd=tmp, check=True)

            result = self._run_on(repo)
            self.assertEqual(result.returncode, 0,
                             "占位符被误报了：\n" + result.stdout)


class VersionConsistencyTests(unittest.TestCase):
    """版本号必须只有一个真相源。

    踩过：``cli.py`` 自己维护了一个 ``VERSION = "0.2.0"``，而包里写的是 0.4.3、
    ``pyproject.toml`` 写的是 0.3.0 —— `--version`、release tag、打包元数据
    三处不一致。这里把三条路都对齐，并防止再漂回去。
    """

    def test_package_and_cli_agree(self):
        from chemfigstudio import __version__
        from chemfigstudio import cli
        self.assertEqual(cli.VERSION, __version__,
                         "cli.VERSION 必须引用 __version__，不能自己写一个")

    def test_pyproject_matches_package(self):
        import re
        from chemfigstudio import __version__
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml 没有 version 字段")
        self.assertEqual(match.group(1), __version__)

    def test_changelog_mentions_the_current_version(self):
        from chemfigstudio import __version__
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [%s]" % __version__, text,
                      "CHANGELOG 里没有当前版本的段落（发布前要补）")


class AuditEntryPointTests(unittest.TestCase):
    """审计入口要一直有效 —— 它引用的路径不存在就等于没有入口。"""

    DOC = REPO / "docs" / "AUDIT.md"

    def test_audit_doc_exists(self):
        self.assertTrue(self.DOC.is_file(), "docs/AUDIT.md 不见了")

    def test_it_points_at_real_files(self):
        """文档里用反引号提到的仓库内路径，必须真的存在。

        否则下一个人照着找会扑空 —— 而"入口指错"比没有入口更糟。
        """
        import re
        text = self.DOC.read_text(encoding="utf-8")
        mentioned = set(re.findall(r"`([A-Za-z0-9_./-]+\.(?:md|py|json|sh|bat))`", text))
        missing = []
        for rel in sorted(mentioned):
            # 只看真正的相对路径（含 /）；`core.py` 这种简称不当作路径检查
            if "/" not in rel:
                continue
            if not (REPO / rel).exists():
                missing.append(rel)
        self.assertEqual(missing, [], "AUDIT.md 引用了不存在的路径：%s" % missing)

    def test_it_records_the_previous_audit_baseline(self):
        """必须写清上一轮的结论在哪 —— 不然新 agent 会重跑一遍。"""
        text = self.DOC.read_text(encoding="utf-8")
        self.assertIn("audit/2026-10-16", text)
        self.assertIn("uipromise-01", text, "已知未修的开口要点名")


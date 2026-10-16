"""macOS 原生外壳（Swift + WKWebView）相关的测试。

分三层，都不需要图形界面：

1. 源码/脚本自检（Swift 源码关键点、构建脚本语法）
2. 已构建产物校验（dist/ChemFigStudio.app 存在时：plist / 内嵌源码 / 可执行）
3. 父进程看门狗集成测试（外壳被强杀后服务要自己收尸）
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SWIFT_SRC = REPO / "native" / "macos" / "ChemFigStudio.swift"
BUILD_SCRIPT = REPO / "scripts" / "build-macos-app.sh"
ICON_SCRIPT = REPO / "scripts" / "make-icon.py"
APP = REPO / "dist" / "ChemFigStudio.app"

MACOS = sys.platform == "darwin"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class NativeSourceTests(unittest.TestCase):
    """源码与脚本自检（任何平台都能跑）。"""

    def test_files_exist(self):
        for path in (SWIFT_SRC, BUILD_SCRIPT, ICON_SCRIPT):
            self.assertTrue(path.exists(), "缺少 %s" % path)

    def test_swift_uses_webkit_and_kills_child(self):
        source = SWIFT_SRC.read_text(encoding="utf-8")
        for marker in ("import WebKit", "WKWebView", "findFreePort",
                       "startServer", "stopServer", "applicationWillTerminate",
                       "--parent-pid", "nonPersistent"):
            self.assertIn(marker, source, "Swift 源码里缺少 %r" % marker)

    def test_swift_has_no_business_logic(self):
        """外壳必须保持"零逻辑"：出现 api/core 这类字样说明有人在里面写业务了。"""
        source = SWIFT_SRC.read_text(encoding="utf-8")
        for forbidden in ("C_H_BONDS", "kJ/g", "kJ mol", "draw_chain", "figure"):
            self.assertNotIn(forbidden, source,
                             "原生外壳里出现了业务逻辑痕迹：%r" % forbidden)

    @unittest.skipUnless(shutil.which("bash"), "没有 bash")
    def test_build_script_syntax(self):
        result = subprocess.run(["bash", "-n", str(BUILD_SCRIPT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class BuiltBundleTests(unittest.TestCase):
    """dist/ChemFigStudio.app 存在时才校验（没构建就跳过）。"""

    def setUp(self):
        if not APP.is_dir():
            self.skipTest("尚未构建 dist/ChemFigStudio.app（先跑 make bundle）")

    def test_plist_is_valid(self):
        plist = APP / "Contents" / "Info.plist"
        self.assertTrue(plist.exists())
        result = subprocess.run(["plutil", "-lint", str(plist)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_executable_and_embedded_source(self):
        exe = APP / "Contents" / "MacOS" / "ChemFigStudio"
        self.assertTrue(os.access(exe, os.X_OK), "可执行文件缺失或没有执行权限")
        self.assertLess(exe.stat().st_size, 5 * 1024 * 1024, "外壳不该这么大")
        pkg = APP / "Contents" / "Resources" / "src" / "chemfigstudio" / "__init__.py"
        self.assertTrue(pkg.exists(), "没有内嵌 Python 源码")

    def test_bundle_is_small(self):
        total = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file())
        # 内嵌 Pillow 后约 13 MB；远超这个数字说明混进了不该带的东西
        self.assertLess(total, 60 * 1024 * 1024,
                        "整包 %.1f MB，超出预期" % (total / 1e6))


@unittest.skipUnless(MACOS, "看门狗行为在 macOS/Linux 上验证")
class ParentWatchdogTests(unittest.TestCase):
    """服务端 --parent-pid：父进程没了就自己退出（防孤儿进程）。"""

    def test_server_exits_when_parent_dies(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")

        # 用一个"中间父进程"启动服务：它启动完就退出，服务随即变成孤儿
        wrapper = (
            "import os, subprocess, sys;"
            "p = subprocess.Popen([sys.executable, '-m', 'chemfigstudio.gui.server',"
            " '--port', '0', '--quiet', '--parent-pid', str(os.getpid())],"
            " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL);"
            "print(p.pid, flush=True)"
        )
        result = subprocess.run([sys.executable, "-c", wrapper], capture_output=True,
                                text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        server_pid = int(result.stdout.strip())
        self.addCleanup(self._kill, server_pid)

        deadline = time.time() + 6
        while time.time() < deadline:
            if not _pid_alive(server_pid):
                return
            time.sleep(0.2)
        self.fail("父进程退出后服务仍在运行（pid %d）——看门狗没生效" % server_pid)

    @staticmethod
    def _kill(pid: int) -> None:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


class ServerCliFlagTests(unittest.TestCase):
    def test_help_mentions_parent_pid_and_quiet(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-m", "chemfigstudio.gui.server", "--help"],
            capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--parent-pid", result.stdout)
        self.assertIn("--quiet", result.stdout)


if __name__ == "__main__":
    unittest.main()


class BuildScriptTests(unittest.TestCase):
    """回归：构建脚本必须能在**不给 --arch**时正常跑，且内嵌**当前**前端。

    踩过的坑（两个）：
    1. 给 `--arch` 加支持时用了 bash 数组，bash 3.2 + `set -u` 下空数组展开会报
       `unbound variable` → 普通 `make bundle` 直接失败（还先 rm 掉了旧 app）。
    2. 打包是**前端快照**：改完 `gui/static/` 不重建，app 里就是旧界面
       （曾经出现「一打开卡在导出弹层」＝旧 bundle 缺 `.sheet[hidden]` 修复）。
    """

    ROOT = pathlib.Path(__file__).resolve().parents[1]

    def test_default_build_embeds_current_frontend(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = pathlib.Path(tmp)
            # 用假的 swiftc 顶替真编译（本测试只验证脚本逻辑与内嵌内容）
            stub_bin = workspace / "bin"
            stub_bin.mkdir()
            swiftc = stub_bin / "swiftc"
            swiftc.write_text("""#!/bin/bash
out=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac
done
[ -n "$out" ] && printf '#!/bin/sh\\n' > "$out" && chmod +x "$out"
exit 0
""", encoding="utf-8")
            swiftc.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = str(stub_bin) + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                ["bash", "scripts/build-macos-app.sh", "--no-vendor",
                 "--out", str(workspace / "dist")],
                cwd=str(self.ROOT), env=env, capture_output=True, text=True, timeout=180)

            combined = (result.stdout or "") + (result.stderr or "")
            self.assertNotIn("unbound variable", combined,
                             "构建脚本在默认（不带 --arch）路径下炸了：\n" + combined[-800:])

            app = workspace / "dist" / "ChemFigStudio.app"
            self.assertTrue(app.is_dir(), "没产出 app：\n" + combined[-800:])
            css = (app / "Contents/Resources/src/chemfigstudio/gui/static/style.css")
            self.assertTrue(css.is_file(), "app 里没内嵌前端")
            self.assertIn(".sheet[hidden]", css.read_text(encoding="utf-8"),
                          "app 内嵌的是旧前端（缺少弹层修复）——改完 gui/static 要重建 bundle")

    def test_repo_bundle_is_not_stale(self):
        """仓库里现存的 bundle（若有）内嵌的前端必须与源码一致，否则提示重建。"""
        app = self.ROOT / "dist" / "ChemFigStudio.app"
        source = self.ROOT / "src" / "chemfigstudio" / "gui" / "static" / "style.css"
        embedded = app / "Contents/Resources/src/chemfigstudio/gui/static/style.css"
        if not embedded.is_file():
            self.skipTest("仓库里没有构建好的 bundle")
        self.assertEqual(embedded.read_text(encoding="utf-8"),
                         source.read_text(encoding="utf-8"),
                         "dist 里的 app 内嵌前端与源码不一致 → 跑 make bundle 重建")

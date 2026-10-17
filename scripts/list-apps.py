#!/usr/bin/env python3
"""列出 dist/ 里构建好的 .app，并说清**哪个该拷给别人**。

为什么需要它（踩过的坑）：`make bundle` 和 `make bundle-universal` 会产出不同名字的
.app；早期版本的 universal 构建还会在两个中间目录里各留一份 —— 于是 `dist/` 里躺着
三四个**图标一模一样**的 app，Finder/Launchpad/Dock 里根本分不清该点哪个，也不知道
哪个是最新的。

用法：
    make apps
    python3 scripts/list-apps.py [dist 目录]
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _plist(app, key):
    path = os.path.join(app, "Contents", "Info.plist")
    try:
        out = subprocess.run(["/usr/libexec/PlistBuddy", "-c",
                              "Print :%s" % key, path],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _architectures(app):
    exe = os.path.join(app, "Contents", "MacOS", "ChemFigStudio")
    if not os.path.isfile(exe):
        return "—"
    try:
        out = subprocess.run(["lipo", "-archs", exe],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "—"
    except (OSError, subprocess.SubprocessError):
        return "—"


def _size_mb(app):
    total = 0
    for root, _dirs, files in os.walk(app):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / (1024 * 1024)


def main(argv):
    dist = argv[1] if len(argv) > 1 else os.path.join(HERE, "dist")
    if not os.path.isdir(dist):
        print("还没有 dist/ —— 先跑 make bundle（小包）或 make bundle-universal（通用包）")
        return 0

    apps = []
    for root, dirs, _files in os.walk(dist):
        for name in list(dirs):
            if name.endswith(".app"):
                apps.append(os.path.join(root, name))
                dirs.remove(name)          # 不往 .app 里面钻
    if not apps:
        print("dist/ 里没有 .app —— 先跑 make bundle 或 make bundle-universal")
        return 0

    apps.sort()
    universal = [a for a in apps if "universal" in os.path.basename(a) and "arm64" not in a
                 and "x86_64" not in a]
    plain = [a for a in apps if a.endswith(os.sep + "ChemFigStudio.app")
             and os.path.dirname(a) == dist.rstrip(os.sep)]

    print("dist/ 里的 .app（共 %d 个）：\n" % len(apps))
    for app in apps:
        rel = os.path.relpath(app, HERE)
        arch = _architectures(app)
        version = _plist(app, "CFBundleShortVersionString") or "?"
        stamp = datetime.datetime.fromtimestamp(os.path.getmtime(app))
        print("  %-46s  %-14s v%-6s %6.1f MB  %s"
              % (rel, arch, version, _size_mb(app), stamp.strftime("%m-%d %H:%M")))

    print("\n该拷给别人哪个：")
    if universal:
        print("  → %s" % os.path.relpath(universal[0], HERE))
        print("    通用二进制（Apple Silicon + Intel 都能跑），这是给别人的那个。")
    elif plain:
        print("  → %s" % os.path.relpath(plain[0], HERE))
        print("    只含 %s 架构：对方 CPU 不同就跑不起来，跨机器分发请先跑 "
              "make bundle-universal。" % _architectures(plain[0]))
    if len(apps) > 2 or (universal and plain):
        print("\n提示：dist/ 里有多个 .app 是正常的（一个是本机自用、一个是通用包），")
        print("      但如果你看到 universal-arm64/ 之类的**中间产物**，说明它是旧版本残留 ——")
        print("      现在的构建脚本用临时目录，跑完会自己删掉；可以安全删除这些残留。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

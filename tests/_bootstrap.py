"""测试引导：让测试**不读用户真实设置**（否则结果随机器变化）。

被各测试文件第一行 ``import _bootstrap`` 引入（``unittest discover -s tests``
会把 ``tests/`` 放进 sys.path，所以是顶层模块导入）。

隔离内容：
* ``CHEMFIG_CONFIG_DIR`` → 仓库内 dist/test-cfg（临时目录，不在家目录）
* ``CHEMFIG_LANG``      → zh-CN（断言不依赖机器语言；需要别的语言时用 ``--lang`` 显式传）
* ``CHEMFIG_PLUGINS``   → off（插件测试会显式指定临时目录）
"""

from __future__ import annotations

import os
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
os.environ.setdefault("CHEMFIG_CONFIG_DIR", str(_ROOT / "dist" / "test-cfg"))
os.environ.setdefault("CHEMFIG_LANG", "zh-CN")
# 默认不扫插件目录：否则仓库 plugins/ 里的示例会污染各测试的图类型表
os.environ.setdefault("CHEMFIG_PLUGINS", "off")

"""chemfigstudio.gui — 本地网页界面。

这里**故意不**在导入时引入 ``server``：``python3 -m chemfigstudio.gui.server``
会先导入本包再执行子模块，提前 import 会触发 RuntimeWarning。属性按需加载。
"""

from __future__ import annotations

from typing import Any

__all__ = ["serve", "make_server", "GuiHandler"]


def __getattr__(name: str) -> Any:                     # PEP 562：惰性导出
    if name in __all__:
        from . import server
        return getattr(server, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))

"""chemfigstudio.errors — 统一异常类型。

放在单独模块，避免 ``api`` 与 ``smiles`` 互相 import 形成环。
"""

from __future__ import annotations

__all__ = ["RenderError", "ConfigError"]


class RenderError(ValueError):
    """参数不合法、SMILES 无法解析、无法出图。

    * CLI：打印消息到 stderr，退出码 1
    * GUI：HTTP 400 + ``{"ok": false, "error": "..."}``
    """


class ConfigError(RuntimeError):
    """运行环境问题（缺字体、缺 Pillow、RDKit 没装等）。"""

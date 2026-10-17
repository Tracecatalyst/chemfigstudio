"""chemfigstudio — 课本风格化学图生成器（示性式 + 氧上孤对电子）。

分层：

* :mod:`chemfigstudio.core`     绘图内核（字体、上下标排版、图元、裁边）
* :mod:`chemfigstudio.figures`  四种图的构造函数
* :mod:`chemfigstudio.api`      参数表 + 校验 + 统一入口（GUI/CLI 都走这里）
* :mod:`chemfigstudio.smiles`   SMILES / 名称 → 碳数（RDKit 可选）
* :mod:`chemfigstudio.cli`      命令行
* :mod:`chemfigstudio.settings` 用户设置（输出目录 / 语言 / 导出默认值）
* :mod:`chemfigstudio.i18n`     国际化（zh-CN / en）
* :mod:`chemfigstudio.gui`      本地网页界面

快速上手::

    import chemfigstudio as cf
    cf.render("alcohol", out="butanol.png", params={"smiles": "CCCCO", "delta_h": "-36.1"})
"""

from __future__ import annotations

from . import api, core, figures, i18n, settings
from .api import FIGURES, FigureSpec, ParamSpec, list_figures, render
from .errors import ConfigError, RenderError
from .figures import FigureResult

__version__ = "0.5.1"

__all__ = [
    "__version__",
    "api", "core", "figures", "i18n", "settings",
    "render", "list_figures", "FIGURES", "FigureSpec", "ParamSpec",
    "FigureResult", "RenderError", "ConfigError",
]

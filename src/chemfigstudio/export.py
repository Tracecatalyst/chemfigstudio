"""chemfigstudio.export — 导出服务：**先探测、再写盘**。

契约要点（、12、13）：

* 「生成」只在画布上预览；**只有本模块会写文件**
* 目标文件已存在且未确认覆盖 → 返回 :class:`ExportPlan` 让界面先问用户；
  用户取消 → **一个字节都不写**（旧文件的 mtime 不变）
* 目录不可写 → 抛 :class:`RenderError`（可读消息），且**不留半成品**
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import api, i18n
from .errors import RenderError
from .settings import VALID_FORMATS

__all__ = ["ExportRequest", "ExportPlan", "plan", "run", "extension_for"]


@dataclass
class ExportRequest:
    """一次导出请求（全部字段都可由界面/CLI 提供）。"""

    kind: str
    params: Dict[str, Any] = field(default_factory=dict)
    directory: Optional[str] = None
    filename: str = ""
    format: str = "png"
    dpi: int = 300
    transparent: bool = False
    overwrite: bool = False

    def target(self, fmt: Optional[str] = None) -> Path:
        """目标文件路径（``fmt`` 缺省用请求里的格式）。"""
        stem = Path(self.filename or ("figure." + self.format)).stem or "figure"
        suffix = extension_for(fmt or self.format)
        base = Path(self.directory).expanduser() if self.directory else Path.cwd()
        return base / (stem + suffix)


@dataclass
class ExportPlan:
    """写盘前的探测结果：将要写哪些文件、其中哪些已存在。"""

    request: ExportRequest
    targets: List[Path]

    @property
    def existing(self) -> List[Path]:
        return [p for p in self.targets if p.exists()]

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.existing) and not self.request.overwrite

    def as_dict(self) -> Dict[str, Any]:
        return {"targets": [str(p) for p in self.targets],
                "existing": [str(p) for p in self.existing],
                "needs_confirmation": self.needs_confirmation}


def extension_for(fmt: str) -> str:
    clean = str(fmt or "png").lower().lstrip(".")
    return "." + (clean if clean in VALID_FORMATS else "png")


def _ensure_writable(directory: Path) -> None:
    """目录可创建且可写；否则抛可读错误。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError(i18n.t("export.err.dir_unwritable", path=directory)) from exc
    if not os.access(directory, os.W_OK):
        raise RenderError(i18n.t("export.err.dir_unwritable", path=directory))


def plan(request: ExportRequest) -> ExportPlan:
    """算出将写入的文件（不落盘）。"""
    fmt = str(request.format or "png").lower().lstrip(".")
    if fmt not in VALID_FORMATS:
        raise RenderError(i18n.t("export.err.bad_format", format=request.format))
    targets = [request.target(fmt)]
    return ExportPlan(request=request, targets=targets)


def run(request: ExportRequest):
    """执行导出；返回 :class:`chemfigstudio.figures.FigureResult`。

    调用前请先看 :meth:`ExportPlan.needs_confirmation` ——
    本函数会**覆盖**已存在的文件（这是调用方的责任）。
    """
    prepared = plan(request)
    if prepared.needs_confirmation:
        raise RenderError(i18n.t("export.confirm_overwrite",
                                 path=", ".join(str(p) for p in prepared.existing)))

    target = prepared.targets[0]
    _ensure_writable(target.parent)

    result = api.render(request.kind, out=str(target), params=dict(request.params or {}),
                        formats=(str(request.format).lower().lstrip("."),),
                        dpi=int(request.dpi or 300),
                        transparent=bool(request.transparent))
    return result

"""chemfigstudio.document — 编辑器文档模型（``*.cfgd.json``）。

契约 §5.1 / §8：

* ``formatVersion`` **只增不改**；读到更高版本 → 拒绝打开并给可读提示（不静默降级）
* **未知字段保留**（旧版本不删新版本写的字段）
* 两种模式（Q4=C）：``mode="molecule"`` 单分子 / ``mode="figure"`` 一张图（1..n 个分子）
* 草稿存在配置目录的 ``drafts/``，**不写用户输出目录**（/3）
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import i18n
from .errors import ConfigError, RenderError
from .settings import config_dir

__all__ = ["FORMAT_VERSION", "Document", "drafts_dir", "list_drafts", "load_draft"]

FORMAT_VERSION = 1
DEFAULT_STYLE = {"font": "Arial", "oxygenColour": "#D61F1F", "scale": 1.0}
DEFAULT_EXPORT = {"format": "png", "dpi": 300, "transparent": False, "filename": "figure.png"}


def drafts_dir() -> Path:
    return config_dir() / "drafts"


def _slug(text: str, fallback: str = "document") -> str:
    clean = re.sub(r"[^0-9A-Za-z_-]+", "-", (text or "").strip()).strip("-").lower()
    return clean or fallback


@dataclass
class Document:
    """一份可编辑文档。"""

    mode: str = "figure"
    figure: Dict[str, Any] = field(default_factory=lambda: {"kind": "alcohol", "params": {}})
    molecules: List[Dict[str, Any]] = field(default_factory=list)
    layers: List[Dict[str, Any]] = field(default_factory=lambda: [{"id": "L1", "name": "structure",
                                                                  "visible": True}])
    style: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_STYLE))
    export: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_EXPORT))
    extra: Dict[str, Any] = field(default_factory=dict)      # 未知字段保留
    path: Optional[Path] = None
    saved_at: float = 0.0

    # ------------------------------------------------------------------ 构造
    @classmethod
    def new(cls, kind: str = "alcohol", params: Optional[Dict[str, Any]] = None,
            mode: str = "figure") -> "Document":
        return cls(mode=mode, figure={"kind": kind, "params": dict(params or {})})

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], path: Optional[Path] = None) -> "Document":
        version = raw.get("formatVersion")
        if isinstance(version, int) and version > FORMAT_VERSION:
            raise RenderError(i18n.t("error.doc_version_too_new", version=version))
        known = {"formatVersion", "mode", "figure", "molecules", "layers", "style", "export",
                 "savedAt"}
        return cls(
            mode=str(raw.get("mode") or "figure"),
            figure=raw.get("figure") or {"kind": "alcohol", "params": {}},
            molecules=list(raw.get("molecules") or []),
            layers=list(raw.get("layers") or [{"id": "L1", "name": "structure", "visible": True}]),
            style={**DEFAULT_STYLE, **(raw.get("style") or {})},
            export={**DEFAULT_EXPORT, **(raw.get("export") or {})},
            extra={k: v for k, v in raw.items() if k not in known},
            path=Path(path) if path else None,
            saved_at=float(raw.get("savedAt") or 0.0),
        )

    @classmethod
    def load(cls, path) -> "Document":
        target = Path(path)
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RenderError("document not found: %s" % target) from exc
        except json.JSONDecodeError as exc:
            raise RenderError("document is not valid JSON: %s" % target) from exc
        if not isinstance(raw, dict):
            raise RenderError("document must be a JSON object: %s" % target)
        return cls.from_dict(raw, path=target)

    # ------------------------------------------------------------------ 序列化
    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "formatVersion": FORMAT_VERSION,
            "mode": self.mode,
            "figure": dict(self.figure),
            "molecules": [dict(m) for m in self.molecules],
            "layers": [dict(layer) for layer in self.layers],
            "style": dict(self.style),
            "export": dict(self.export),
            "savedAt": self.saved_at or time.time(),
        }
        payload.update(self.extra)                     # 未知字段写回
        return payload

    def save(self, path=None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ConfigError("document has no path")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".cfgd-", suffix=".json", dir=str(target.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.as_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp, target)
        except OSError as exc:
            raise ConfigError("cannot write document: %s" % target) from exc
        self.path = target
        self.saved_at = time.time()
        return target

    # ------------------------------------------------------------------ 行为
    def set_figure(self, kind: str, params: Optional[Dict[str, Any]] = None) -> None:
        """「应用」：只更新文档，不落盘、不导出。"""
        self.figure = {"kind": str(kind), "params": dict(params or {})}

    def render_target(self) -> Tuple[str, Dict[str, Any]]:
        """交给 :func:`chemfigstudio.api.render` 的 (kind, params)。"""
        return str(self.figure.get("kind") or "alcohol"), dict(self.figure.get("params") or {})

    def add_layer(self, name: str) -> Dict[str, Any]:
        layer = {"id": "L%d" % (len(self.layers) + 1), "name": name or "layer", "visible": True}
        self.layers.append(layer)
        return layer

    def toggle_layer(self, layer_id: str, visible: Optional[bool] = None) -> None:
        for layer in self.layers:
            if layer.get("id") == layer_id:
                layer["visible"] = (not layer.get("visible", True)) if visible is None else bool(visible)

    def visible_layers(self) -> List[Dict[str, Any]]:
        return [layer for layer in self.layers if layer.get("visible", True)]

    def save_draft(self) -> Path:
        """存草稿到配置目录（不写用户输出目录）。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "%s-%s-%s.cfgd.json" % (stamp, _slug(self.figure.get("kind", "figure")),
                                       uuid.uuid4().hex[:6])
        return self.save(drafts_dir() / name)


def list_drafts(limit: int = 20) -> List[Dict[str, Any]]:
    """列出草稿（最新在前）；读不动的草稿跳过而不是报错。"""
    directory = drafts_dir()
    if not directory.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.cfgd.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            document = Document.load(path)
        except Exception:
            continue
        items.append({"id": path.name, "path": str(path),
                      "modified": path.stat().st_mtime,
                      "kind": document.figure.get("kind"), "mode": document.mode})
    return items


def load_draft(name: str) -> Document:
    """按 id 读草稿；名字限定在草稿目录内（防目录穿越）。"""
    safe = Path(name).name
    target = drafts_dir() / safe
    if not target.is_file():
        raise RenderError("draft not found: %s" % safe)
    return Document.load(target)

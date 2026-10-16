"""chemfigstudio.settings — 用户设置（可视化修改，JSON 持久化）。

位置（契约 §5.2）::

    macOS  ：~/Library/Application Support/chemfigstudio/settings.json
    其它平台：~/.config/chemfigstudio/settings.json
    测试/便携：环境变量 CHEMFIG_CONFIG_DIR 指向的目录

规则（契约 §8）：
* **未知字段保留**（向前兼容：新版本写的字段不被旧版本删掉）
* 文件损坏 → 备份为 ``settings.json.bak`` 并回默认值，不抛异常
* 写入用"临时文件 + 原子替换"，避免半截文件
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import i18n
from .errors import ConfigError

__all__ = ["Settings", "settings_path", "config_dir", "DEFAULTS", "VALID_FORMATS"]

#: 支持的导出格式（与契约 §5.4 一致）
VALID_FORMATS = ("png", "svg", "pdf")

#: 默认值（契约 §5.2）
DEFAULTS: Dict[str, Any] = {
    "language": "auto",          # "auto" = 跟随系统；或 "zh-CN" / "en"
    "outputDir": "",             # 空 = 用系统"文稿/chemfigstudio"
    "defaultFormat": "png",
    "defaultDpi": 300,
    "transparent": False,
    "pluginsDir": "",            # 空 = <config_dir>/plugins
    # 除 outputDir 之外，还允许 GUI/批量写盘的目录（绝对路径列表）。
    # 默认空 = 只能写 outputDir —— HTTP 请求来自浏览器，不能让它把文件写到任意位置。
    "allowedWriteDirs": [],
    "recentDocuments": [],
    "windowState": {},
}
_LIST_FIELDS = ("recentDocuments", "allowedWriteDirs")
_INT_FIELDS = ("defaultDpi",)


def config_dir() -> Path:
    """配置目录（可用 CHEMFIG_CONFIG_DIR 覆盖，便于测试与便携部署）。"""
    override = os.environ.get("CHEMFIG_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "chemfigstudio"
    return Path.home() / ".config" / "chemfigstudio"


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    """用户设置。字段访问走 :meth:`get` / :meth:`set`，未知键也存在 ``extra`` 里。"""

    values: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    path: Path = field(default_factory=settings_path)
    extra: Dict[str, Any] = field(default_factory=dict)     # 本版本不认识的字段
    loaded_from_disk: bool = False
    repaired: bool = False                                  # 是否因损坏而回默认值

    # ------------------------------------------------------------------ 读写
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        """从磁盘读取；不存在或损坏都返回可用对象（损坏时备份原文件）。"""
        target = Path(path) if path else settings_path()
        obj = cls(path=target)
        if not target.exists():
            return obj
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("设置文件顶层必须是 JSON 对象")
        except Exception:
            backup = target.with_suffix(".json.bak")
            try:
                shutil.copy2(target, backup)
            except OSError:
                pass
            obj.repaired = True
            return obj

        values = dict(DEFAULTS)
        extra: Dict[str, Any] = {}
        for key, value in raw.items():
            if key in DEFAULTS:
                values[key] = value
            else:
                extra[key] = value                          # 未知字段保留

        obj.values = obj._sanitized(values)
        obj.extra = extra
        obj.loaded_from_disk = True
        return obj

    def save(self) -> Path:
        """原子写入（临时文件 + replace），返回最终路径。

        写不了（权限/只读/沙箱）时抛 :class:`ConfigError`，消息里带路径 ——
        调用方（CLI/GUI）负责呈现成人话，绝不抛裸 OSError。
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(i18n.t("error.settings_unwritable", path=self.path)) from exc
        payload = dict(self.values)
        payload.update(self.extra)                          # 未知字段写回
        try:
            fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json",
                                       dir=str(self.path.parent))
        except OSError as exc:
            raise ConfigError(i18n.t("error.settings_unwritable", path=self.path)) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return self.path

    # ------------------------------------------------------------------ 访问
    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, *, save: bool = False) -> None:
        """设置一项；``save=True`` 立刻落盘。未知键进 ``extra``。"""
        if key in DEFAULTS:
            self.values[key] = value
        else:
            self.extra[key] = value
        if save:
            self.save()

    def update(self, changes: Dict[str, Any], *, save: bool = False) -> List[str]:
        """批量设置，返回被改动（或新增）的键名列表。"""
        changed: List[str] = []
        for key, value in (changes or {}).items():
            before = self.values.get(key) if key in DEFAULTS else self.extra.get(key)
            self.set(key, value)
            after = self.values.get(key) if key in DEFAULTS else self.extra.get(key)
            if before != after:
                changed.append(key)
        if save and changed:
            self.save()
        return changed

    # ------------------------------------------------------------------ 杂项
    def output_dir(self) -> Path:
        """导出目录：设置为空时用 ``~/Documents/chemfigstudio``。"""
        configured = str(self.get("outputDir") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return Path.home() / "Documents" / "chemfigstudio"

    def plugins_dir(self) -> Path:
        configured = str(self.get("pluginsDir") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return config_dir() / "plugins"

    def as_dict(self) -> Dict[str, Any]:
        data = dict(self.values)
        data["_path"] = str(self.path)
        data["_extra"] = dict(self.extra)
        data["_resolved"] = {"outputDir": str(self.output_dir()),
                             "pluginsDir": str(self.plugins_dir())}
        return data

    # ------------------------------------------------------------------ 校验
    @staticmethod
    def _sanitized(values: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(values)
        fmt = str(out.get("defaultFormat", "png")).lower()
        out["defaultFormat"] = fmt if fmt in VALID_FORMATS else "png"

        try:
            dpi = int(out.get("defaultDpi", 300))
        except (TypeError, ValueError):
            dpi = 300
        out["defaultDpi"] = max(72, min(1200, dpi))

        out["transparent"] = bool(out.get("transparent", False))
        if not isinstance(out.get("recentDocuments"), list):
            out["recentDocuments"] = []
        # 允许写盘目录：只收绝对路径（相对路径无法判定边界），去重保序
        raw_dirs = out.get("allowedWriteDirs")
        if not isinstance(raw_dirs, list):
            raw_dirs = []
        cleaned: list = []
        for entry in raw_dirs:
            text = str(entry or "").strip()
            if not text or not os.path.isabs(os.path.expanduser(text)):
                continue
            text = os.path.expanduser(text)
            if text not in cleaned:
                cleaned.append(text)
        out["allowedWriteDirs"] = cleaned
        if not isinstance(out.get("windowState"), dict):
            out["windowState"] = {}
        lang = str(out.get("language", "auto"))
        out["language"] = lang if lang in ("auto", "zh-CN", "en") else "auto"
        return out

    def add_recent(self, path: str, limit: int = 10) -> None:
        """记录最近打开的文档（去重、最新在前、限长）。"""
        items = [p for p in self.get("recentDocuments", []) if p != path]
        items.insert(0, str(path))
        self.values["recentDocuments"] = items[:limit]

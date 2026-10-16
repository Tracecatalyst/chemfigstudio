"""chemfigstudio.i18n — 国际化（中文 / English）。

契约要点（§5、–18）：

* 语言目录放在包内 ``locales/<code>.json``，**新增语言 = 加一个文件**（架构为 R3 留位）
* 中英两个 catalog 的 key 集合必须完全一致（有测试断言）
* 取词回退链：请求语言 → ``en`` → key 本身（永不抛异常、永不显示空白）
* 默认语言跟随系统；设置里可强制覆盖（``auto`` / ``zh-CN`` / ``en``）
* 支持 ``{name}`` 占位符与 ``.format(**kwargs)`` 风格替换
"""

from __future__ import annotations

import json
import locale
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["t", "text", "set_language", "current_language", "available_languages",
           "system_language", "resolve_language", "catalog", "missing_keys"]

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LANGUAGE = "en"          # 回退语言
SUPPORTED = ("zh-CN", "en")

_state: Dict[str, str] = {"language": "auto"}


# --------------------------------------------------------------------- 语言选择

def system_language() -> str:
    """按系统语言猜一个受支持的语言代码（猜不到回 zh-CN 之外就用 en）。"""
    candidates: List[str] = []
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            candidates.append(value)
    try:
        candidates.append(locale.getdefaultlocale()[0] or "")
    except Exception:                                        # pragma: no cover
        pass
    candidates.append(os.environ.get("CHEMFIG_LANG", ""))

    for raw in candidates:
        text = (raw or "").lower().replace("_", "-")
        if text.startswith("zh"):
            return "zh-CN"
        if text.startswith("en"):
            return "en"

    # macOS 上更可靠的一招：读系统偏好里的 AppleLanguages
    if sys.platform == "darwin":                             # pragma: no cover
        try:
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                                 capture_output=True, text=True, timeout=5).stdout
            if "zh" in out.lower():
                return "zh-CN"
            if "en" in out.lower():
                return "en"
        except Exception:
            pass
    return "zh-CN"


def resolve_language(requested: Optional[str] = None) -> str:
    """把 ``auto`` / 别名解析成受支持的代码。"""
    raw = (requested or _state.get("language") or "auto").strip()
    if raw in ("", "auto", "system"):
        return system_language()
    low = raw.lower().replace("_", "-")
    if low.startswith("zh"):
        return "zh-CN"
    if low.startswith("en"):
        return "en"
    return low if low in SUPPORTED else DEFAULT_LANGUAGE


def set_language(code: Optional[str]) -> str:
    """设定当前语言（``auto`` 允许），返回解析后的代码。"""
    _state["language"] = (code or "auto").strip() or "auto"
    return resolve_language(_state["language"])


def current_language() -> str:
    return resolve_language(_state.get("language"))


def available_languages() -> List[Dict[str, str]]:
    """可选语言列表（GUI 的下拉框用）。"""
    labels = {"zh-CN": "中文", "en": "English"}          # 语言名用各自语言写，不翻译
    out = [{"code": "auto", "label": t("settings.language_auto")}]
    for code in SUPPORTED:
        out.append({"code": code, "label": labels.get(code, code)})
    return out


# ------------------------------------------------------------------ 目录与取词

@lru_cache(maxsize=8)
def catalog(code: str) -> Dict[str, str]:
    """读某个语言的目录；文件缺失时返回空字典（不抛异常）。"""
    path = LOCALES_DIR / ("%s.json" % code)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: str(v) for k, v in data.items() if not k.startswith("_")}


def text(key: str, lang: Optional[str] = None, **kwargs: Any) -> str:
    """**纯函数**取词：不读写全局语言状态，因此可被多线程安全并发调用。

    回退链：``lang``（默认当前语言）→ en → key 本身。
    占位符用 ``str.format``，格式化失败时原样返回（不让翻译问题把程序搞崩）。
    """
    requested = resolve_language(lang) if lang is not None else current_language()
    chosen: Optional[str] = None
    for code in (requested, DEFAULT_LANGUAGE):
        found = catalog(code).get(key)
        if found is not None:
            chosen = found
            break
    if chosen is None:
        chosen = key
    if kwargs:
        try:
            return chosen.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return chosen
    return chosen


def t(key: str, **kwargs: Any) -> str:
    """取当前语言的文案（对 :func:`text` 的薄封装）。"""
    return text(key, None, **kwargs)


def missing_keys(code: str) -> List[str]:
    """相对 en 缺失的 key（测试与 CI 用）。"""
    base = set(catalog(DEFAULT_LANGUAGE))
    return sorted(base - set(catalog(code)))


def extra_keys(code: str) -> List[str]:
    """相对 en 多出来的 key。"""
    base = set(catalog(DEFAULT_LANGUAGE))
    return sorted(set(catalog(code)) - base)

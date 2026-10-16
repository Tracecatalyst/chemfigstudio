"""chemfigstudio.plugins — 自己加图类型（阶段④）。

两种插件形式：

1. **Python 插件** ``*.py``：定义 ``register(api)``，用 ``api.add_figure(...)`` 注册新图类型。
   插件能拿到内置图用的同一套底层工具（``api.surface`` / ``api.core``）。
2. **JSON 预置** ``*.figure.json``：把某个内置图类型配上默认参数，另起一个名字
   （例如给报告固定一份"丁醇 + ΔH"的常用设置），无需写代码。

扫描目录（按顺序，先扫到的先注册）：

1. ``settings.pluginsDir``（用户配置目录里，默认 ``<配置目录>/plugins``）
2. 仓库根 ``plugins/``（随项目走，便于版本管理）

**安全边界**：插件是**受信任的本地代码**，加载即执行 —— 只放自己写的/信得过的文件。
坏插件不会拖垮程序：每个插件的错误会被记录（``PluginInfo.error``），其余插件照常加载。
**内置图类型不可被覆盖**（会被拒绝并记录错误），避免插件静默改变既有输出。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import i18n
from .errors import RenderError

__all__ = ["PluginInfo", "PluginAPI", "plugin_dirs", "load_all", "loaded", "plugin_report"]

PY_SUFFIX = ".py"
JSON_SUFFIX = ".figure.json"


@dataclass
class PluginInfo:
    """一个插件文件的结果（成功或失败都记录）。"""

    name: str
    path: str
    kinds: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "path": self.path, "kinds": list(self.kinds),
                "ok": self.ok, "error": self.error}


class PluginAPI:
    """交给插件的门面：只暴露稳定的东西，不暴露内部状态。"""

    def __init__(self, source: str) -> None:
        self.source = source
        self.kinds: List[str] = []

    # ---------------------------------------------------------------- 图类型
    def add_figure(self, kind: str, runner: Callable[..., Any], *,
                   title: str = "", description: str = "",
                   params: Optional[Sequence[Any]] = None,
                   default_filename: str = "") -> Any:
        """注册一个新图类型。``kind`` 不能与内置类型重名。"""
        from . import api                                     # 延迟导入避免循环
        clean = str(kind or "").strip()
        if not clean:
            raise RenderError("plugin figure needs a non-empty kind")
        if clean in api.BUILTIN_KINDS:
            raise RenderError("plugin cannot override built-in figure kind %r" % clean)
        if clean in api.FIGURES:
            raise RenderError("figure kind %r is already registered" % clean)
        if not callable(runner):
            raise RenderError("plugin figure %r needs a callable runner" % clean)

        spec = api.FigureSpec(
            kind=clean,
            title=title or ("plugin." + clean),
            description=description or "",
            params=tuple(params or ()),
            default_filename=default_filename or (clean + ".png"),
            runner=runner,
        )
        api.FIGURES[clean] = spec
        self.kinds.append(clean)
        return spec

    # ------------------------------------------------------------ 参数便捷式
    def int_param(self, name: str, *, label: str = "", default: int = 0,
                  minimum: Optional[int] = None, maximum: Optional[int] = None,
                  help: str = "") -> Any:
        from . import api
        return api.ParamSpec(name, "int", label or ("param." + name + ".label"),
                             default=default, minimum=minimum, maximum=maximum, help=help)

    def str_param(self, name: str, *, label: str = "", default: str = "",
                  help: str = "") -> Any:
        from . import api
        return api.ParamSpec(name, "str", label or ("param." + name + ".label"),
                             default=default, help=help)

    def choice_param(self, name: str, choices: Sequence[str], *, label: str = "",
                     default: Optional[str] = None, help: str = "") -> Any:
        from . import api
        return api.ParamSpec(name, "choice", label or ("param." + name + ".label"),
                             default=default or (choices[0] if choices else ""),
                             choices=tuple(choices), help=help)

    # ------------------------------------------------------------ 绘制工具
    def surface(self, width: int, height: int, **kwargs: Any):
        """新建画布（双后端：PNG/SVG 一份代码）。"""
        from . import core
        return core.new_surface(width, height, **kwargs)

    @property
    def core(self):
        from . import core
        return core

    @property
    def figures(self):
        from . import figures
        return figures

    def t(self, key: str, **kwargs: Any) -> str:
        return i18n.t(key, **kwargs)


#: 关闭插件扫描的环境变量取值
OFF_VALUES = ("off", "none", "0", "no", "false")


def plugin_dirs() -> List[Path]:
    """插件扫描目录。

    * ``CHEMFIG_PYLIBS`` 式写法：``CHEMFIG_PLUGINS=/path/a:/path/b`` **显式指定**（不再扫默认位置）
    * ``CHEMFIG_PLUGINS=off``：**完全不扫描**（测试与"干净环境"用）
    * 未设置：用户配置目录的 ``plugins/`` + 仓库根 ``plugins/``
    """
    import os
    env = os.environ.get("CHEMFIG_PLUGINS", "").strip()
    if env:
        if env.lower() in OFF_VALUES:
            return []
        return [Path(chunk).expanduser() for chunk in env.split(os.pathsep) if chunk.strip()]
    from .settings import Settings
    cwd = Path(__file__).resolve().parents[2]          # 仓库根
    directories = [Settings.load().plugins_dir(), cwd / "plugins"]
    seen, unique = set(), []
    for directory in directories:
        key = str(directory)
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique


def _load_python(path: Path, info: PluginInfo) -> None:
    module_name = "chemfigstudio_plugin_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RenderError("cannot load plugin module: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)                        # 加载即执行（受信任代码）
    register = getattr(module, "register", None)
    if not callable(register):
        raise RenderError("plugin %s has no register(api) function" % path.name)
    facade = PluginAPI(source=str(path))
    register(facade)
    info.kinds = list(facade.kinds)


def _load_json(path: Path, info: PluginInfo) -> None:
    """JSON 预置：给内置图类型配一套默认参数，另起一个 kind。"""
    from . import api
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError("plugin json is invalid: %s (%s)" % (path.name, exc)) from exc
    if not isinstance(raw, dict):
        raise RenderError("plugin json must be an object: %s" % path.name)
    clean = str(raw.get("kind") or "").strip()
    base = str(raw.get("base") or "").strip()
    if not clean or not base:
        raise RenderError("plugin json needs both 'kind' and 'base': %s" % path.name)
    if base not in api.FIGURES:
        raise RenderError("plugin json base %r is unknown (available: %s)"
                          % (base, ", ".join(sorted(api.FIGURES))))
    preset = dict(raw.get("params") or {})
    base_spec = api.FIGURES[base]

    TRANSPORT = ("formats", "dpi", "transparent", "scale_override")

    def runner(out, _base=base_spec, _preset=preset, **kwargs):
        # 传输参数（formats/dpi/…）不是图参数，必须与预置参数分开
        transport = {k: kwargs.pop(k) for k in TRANSPORT if k in kwargs}
        merged = dict(_preset)
        merged.update({k: v for k, v in kwargs.items() if v not in (None, "")})
        clean = _base.prepare(merged)                     # 补齐默认值 + 校验
        clean.pop("smiles_info", None)
        return _base.runner(out, formats=transport.get("formats", ("png",)),
                            dpi=transport.get("dpi"),
                            transparent=bool(transport.get("transparent")),
                            scale_override=transport.get("scale_override"), **clean)

    facade = PluginAPI(source=str(path))
    facade.add_figure(clean, runner, title=str(raw.get("title") or ("plugin." + clean)),
                      description=str(raw.get("description") or
                                      i18n.t("plugin.preset_of", base=base)),
                      params=base_spec.params,
                      default_filename=str(raw.get("default_filename") or (clean + ".png")))
    info.kinds = list(facade.kinds)


_STATE: Dict[str, Any] = {"loaded": False, "infos": []}


def loaded() -> bool:
    return bool(_STATE["loaded"])


def load_all(force: bool = False) -> List[PluginInfo]:
    """扫描并加载全部插件；返回逐文件结果（幂等，除非 ``force=True``）。"""
    if _STATE["loaded"] and not force:
        return list(_STATE["infos"])
    infos: List[PluginInfo] = []
    for directory in plugin_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.name.startswith("."):
                continue
            is_json = path.name.endswith(JSON_SUFFIX)
            if not (is_json or path.suffix == PY_SUFFIX):
                continue
            info = PluginInfo(name=path.name, path=str(path))
            try:
                if is_json:
                    _load_json(path, info)
                else:
                    _load_python(path, info)
            except Exception as exc:                       # 坏插件不拖垮程序
                info.error = "%s: %s" % (type(exc).__name__, exc)
                info.kinds = []
            infos.append(info)
    _STATE["loaded"] = True
    _STATE["infos"] = infos
    return list(infos)


def plugin_report() -> Dict[str, Any]:
    """给 CLI / GUI / doctor 用的一份概览。"""
    infos = load_all()
    return {"dirs": [str(d) for d in plugin_dirs()],
            "plugins": [i.as_dict() for i in infos],
            "kinds": sorted(k for i in infos for k in i.kinds),
            "errors": [i.as_dict() for i in infos if not i.ok]}

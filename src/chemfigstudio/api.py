"""chemfigstudio.api — 唯一的程序化入口（CLI 与 GUI 都只调用这里）。

分层约定（见 ARCHITECTURE.md）：

    gui/server.py ─┐
                   ├─> api.render() ─> figures.py ─> core.py ─> PNG
    cli.py ────────┘

``figures.py`` 只画图、不做参数校验；参数表、校验、SMILES 解析都收敛在
``api.py``，因此 GUI 的表单可以直接由 :func:`list_figures` 的返回值生成 ——
新增一个参数只需要改一处。
"""

from __future__ import annotations

import json

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import figures, i18n
from . import settings as settings_mod
from .errors import ConfigError, RenderError
from .figures import FigureResult

#: 这些前缀的字符串按 i18n key 解析；其它字符串原样使用
#: （插件作者既可以用 key，也可以直接写死文本 —— 保持向后兼容）
_KEY_PREFIXES = ("param.", "figure.", "settings.", "export.", "gui.", "cli.", "error.", "app.")


def resolve_text(value: Optional[str], lang: Optional[str] = None) -> str:
    """把 i18n key 解析成具体语言文本；不是 key 就原样返回。"""
    if not value:
        return ""
    text = str(value)
    if not text.startswith(_KEY_PREFIXES):
        return text
    # 用纯函数按语言取词：**不改全局状态** → 多线程 HTTP 下不会串语言
    return i18n.text(text, lang)

__all__ = ["ParamSpec", "FigureSpec", "FIGURES", "BUILTIN_KINDS", "RenderError", "ConfigError",
           "list_figures", "render", "preview", "preview_dir", "resolve_output_path"]


# --------------------------------------------------------------------- 参数描述

@dataclass
class ParamSpec:
    """一个可调参数（同时描述 GUI 表单控件）。"""

    name: str
    type: str                     # int | str | choice | bool | smiles
    label: str                    # i18n key 或字面文本
    default: Any = None
    choices: Tuple[str, ...] = ()
    choice_keys: Tuple[str, ...] = ()      # 与 choices 等长的 i18n key（可空）
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    help: str = ""

    def coerce(self, value: Any) -> Any:
        """把来自 JSON / 表单的字符串转成正确类型，并做范围校验。"""
        if value is None or value == "":
            return self.default
        if self.type == "str" and isinstance(value, bool):
            raise RenderError(i18n.t("error.param_not_text", label=self.label_text(),
                                     value=value, type="bool"))
        if self.type == "int":
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                raise RenderError(i18n.t("error.not_int", label=self.label_text(),
                                         value=value))
            if self.minimum is not None and ivalue < self.minimum:
                raise RenderError(i18n.t("error.too_small", label=self.label_text(),
                                         minimum=self.minimum))
            if self.maximum is not None and ivalue > self.maximum:
                raise RenderError(i18n.t("error.too_large", label=self.label_text(),
                                         maximum=self.maximum))
            return ivalue
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on", "是")
        if self.type == "choice":
            text = str(value)
            if self.choices and text not in self.choices:
                raise RenderError(i18n.t("error.bad_choice", label=self.label_text(),
                                         choices=" / ".join(self.choices), value=value))
            return text
        return str(value)

    def label_text(self, lang: Optional[str] = None) -> str:
        return resolve_text(self.label, lang)

    def help_text(self, lang: Optional[str] = None) -> str:
        return resolve_text(self.help, lang)

    def choice_labels(self, lang: Optional[str] = None) -> List[str]:
        """选项的显示文本（没给 choice_keys 时退回原始值）。"""
        if self.choice_keys and len(self.choice_keys) == len(self.choices):
            return [resolve_text(key, lang) for key in self.choice_keys]
        return list(self.choices)

    def as_dict(self, lang: Optional[str] = None) -> Dict[str, Any]:
        return {
            "name": self.name, "type": self.type, "label": self.label_text(lang),
            "default": self.default, "choices": list(self.choices),
            "choice_labels": self.choice_labels(lang),
            "min": self.minimum, "max": self.maximum, "help": self.help_text(lang),
        }


@dataclass
class FigureSpec:
    """一种图：元信息 + 参数表 + 执行函数。"""

    kind: str
    title: str
    description: str
    params: Tuple[ParamSpec, ...]
    runner: Callable[..., FigureResult]
    default_filename: str

    def param_map(self) -> Dict[str, ParamSpec]:
        return {p.name: p for p in self.params}

    def prepare(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """校验 + 类型转换；额外处理 ``smiles`` 字段。"""
        pmap = self.param_map()
        unknown = [k for k in params if k not in pmap]
        if unknown:
            raise RenderError(i18n.t("error.unknown_param",
                                     names=", ".join(sorted(unknown)),
                                     available=", ".join(sorted(pmap))))
        clean: Dict[str, Any] = {}
        for name, spec in pmap.items():
            clean[name] = spec.coerce(params.get(name))

        smiles = clean.pop("smiles", None)
        if smiles:
            from .smiles import resolve_smiles
            info = resolve_smiles(smiles)
            clean["n_carbons"] = info.n_carbons
            if not clean.get("name"):
                clean["name"] = info.name
            clean["smiles_info"] = info          # 供调用方（CLI/GUI）展示
        return clean

    def as_dict(self, lang: Optional[str] = None) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "title": resolve_text(self.title, lang),
            "description": resolve_text(self.description, lang),
            "params": [p.as_dict(lang) for p in self.params],
            "default_filename": resolve_text(self.default_filename, lang),
        }


# ------------------------------------------------------------------------ 注册表

def _spec_alcohol() -> FigureSpec:
    def run(out, n_carbons, name, delta_h, label_style, title,
            formats=("png",), dpi=None, transparent=False, scale_override=None,
            **_ignored) -> FigureResult:
        return figures.alcohol_figure(out, n_carbons=n_carbons, name=name,
                                      delta_h=delta_h, label_style=label_style,
                                      title=title or name, formats=formats, dpi=dpi,
                                      transparent=transparent,
                                      scale_override=scale_override)

    return FigureSpec(
        kind="alcohol",
        title="figure.alcohol.title",
        description="figure.alcohol.desc",
        default_filename="figure.alcohol.filename",
        params=(
            ParamSpec("smiles", "smiles", "param.smiles.label", default="",
                      help="param.smiles.help"),
            ParamSpec("n_carbons", "int", "param.n_carbons.label", default=4, minimum=1, maximum=12,
                      help="param.n_carbons.help"),
            ParamSpec("name", "str", "param.name.label", default=""),
            ParamSpec("delta_h", "str", "param.delta_h.label", default="",
                      help="param.delta_h.help"),
            ParamSpec("label_style", "choice", "param.label_style.label", default="h2", choices=("h2", "ch2"),
                      choice_keys=("param.label_style.h2", "param.label_style.ch2"),
                      help="param.label_style.help"),
            ParamSpec("title", "str", "param.title.label", default=""),
        ),
        runner=run,
    )


def _spec_series() -> FigureSpec:
    default_title = "The alcohol series, from the shortest chain to the longest"

    def run(out, title, show_delta_h, include_methanol=False, entries="",
            formats=("png",), dpi=None, transparent=False,
            scale_override=None, **_ignored) -> FigureResult:
        rows = None
        if str(entries or "").strip():
            try:
                rows = [tuple(row) for row in json.loads(str(entries))]
            except (ValueError, TypeError) as exc:
                raise RenderError(i18n.t("error.series_entries", message=exc)) from exc
        return figures.series_figure(out, entries=rows, title=title or default_title,
                                     show_delta_h=show_delta_h,
                                     include_methanol=bool(include_methanol),
                                     formats=formats, dpi=dpi,
                                     transparent=transparent,
                                     scale_override=scale_override)

    return FigureSpec(
        kind="series",
        title="figure.series.title",
        description="figure.series.desc",
        default_filename="figure.series.filename",
        params=(
            ParamSpec("title", "str", "param.title.label", default=default_title),
            ParamSpec("show_delta_h", "bool", "param.show_delta_h.label", default=True),
            ParamSpec("include_methanol", "bool", "param.include_methanol.label",
                      default=False, help="param.include_methanol.help"),
            ParamSpec("entries", "str", "param.entries.label", default="",
                      help="param.entries.help"),
        ),
        runner=run,
    )


def _spec_reaction() -> FigureSpec:
    def run(out, n_carbons, delta_h_kj_per_mol, formats=("png",), dpi=None,
            transparent=False, scale_override=None,
            **_ignored) -> FigureResult:
        return figures.reaction_figure(out, n_carbons=n_carbons,
                                       delta_h_kj_per_mol=delta_h_kj_per_mol,
                                       formats=formats, dpi=dpi, transparent=transparent,
                                      scale_override=scale_override)

    return FigureSpec(
        kind="reaction",
        title="figure.reaction.title",
        description="figure.reaction.desc",
        default_filename="figure.reaction.filename",
        params=(
            ParamSpec("n_carbons", "int", "param.n_carbons.label", default=4, minimum=1, maximum=12),
            ParamSpec("delta_h_kj_per_mol", "int", "param.delta_h_kj_per_mol.label",
                      default=None, minimum=-99999, maximum=0),
        ),
        runner=run,
    )


def _spec_chcount() -> FigureSpec:
    def run(out, n_carbons, summary, formats=("png",), dpi=None, transparent=False,
            scale_override=None, **_ignored) -> FigureResult:
        return figures.chcount_figure(out, n_carbons=n_carbons, summary=summary or None,
                                      formats=formats, dpi=dpi, transparent=transparent,
                                      scale_override=scale_override)

    return FigureSpec(
        kind="chcount",
        title="figure.chcount.title",
        description="figure.chcount.desc",
        default_filename="figure.chcount.filename",
        params=(
            ParamSpec("n_carbons", "int", "param.n_carbons.label", default=4, minimum=1, maximum=12),
            ParamSpec("summary", "str", "param.summary.label", default=""),
        ),
        runner=run,
    )


FIGURES: Dict[str, FigureSpec] = {s.kind: s for s in
                                  (_spec_alcohol(), _spec_series(),
                                   _spec_reaction(), _spec_chcount())}


# --------------------------------------------------------------------- 对外接口

def list_figures(lang: Optional[str] = None) -> List[Dict[str, Any]]:
    """返回所有图的描述（GUI 用它动态生成表单）；``lang`` 指定语言。

    顺序：内置图在前（固定顺序），插件图在后（按注册顺序）。
    """
    ensure_plugins_loaded()
    builtin = [k for k in BUILTIN_KINDS if k in FIGURES]
    extras = [k for k in FIGURES if k not in BUILTIN_KINDS]
    return [FIGURES[k].as_dict(lang) for k in builtin + extras]


def resolve_output_path(out: Optional[str], kind: str, outdir: Optional[Path] = None) -> Path:
    """决定输出文件路径：给定文件路径 → 用之；只给目录 / 不给出 → 用默认文件名。"""
    spec = FIGURES.get(kind)
    default_name = resolve_text(spec.default_filename) if spec else "figure.png"
    if out:
        text = str(out)
        if text.endswith((os.sep, "/")) or Path(text).expanduser().is_dir():
            return Path(text).expanduser() / default_name
        return Path(text).expanduser()
    base = Path(outdir).expanduser() if outdir else Path.cwd()
    return base / default_name


#: 内置图类型（插件不得覆盖这些名字）
BUILTIN_KINDS: Tuple[str, ...] = ("alcohol", "series", "reaction", "chcount")


def ensure_plugins_loaded() -> None:
    """首次用到图类型表时加载插件（阶段④）。插件自身的错误不会外抛。"""
    from . import plugins
    if not plugins.loaded():
        plugins.load_all()


def preview_dir() -> Path:
    """预览缓存目录（在配置目录里，**不是**用户输出目录 —— 验收 1/2）。"""
    return settings_mod.config_dir() / "preview"


#: 预览最长边上限（屏幕上看足够，避免 3000 px 的预览图拖慢界面）
PREVIEW_MAX_LONGEST = 1200


def preview(kind: str, params: Optional[Dict[str, Any]] = None, *,
            dpi: Optional[int] = 0, transparent: bool = False, svg: bool = False,
            max_longest: int = PREVIEW_MAX_LONGEST) -> FigureResult:
    """画布预览：结果只落在预览缓存目录，用户输出目录保持干净。

    ``dpi=0``（默认）= 1:1 绘制，不做 DPI 放大 —— 预览要的是"快且够看"。
    若图本身仍超过 ``max_longest``，再按比例缩一次（复用 scale_override）。
    """
    directory = preview_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fmt = "svg" if svg else "png"
    target = directory / ("preview." + fmt)

    result = render(kind, out=str(target), params=params, formats=(fmt,),
                    dpi=0 if dpi is None else dpi, transparent=transparent)
    if max_longest and max(result.size) > max_longest:
        # 留 2% 余量：缩放系数是估算值，直接压到上限会因舍入超一点点
        factor = 0.98 * float(max_longest) / float(max(result.size))
        retry = render(kind, out=str(target), params=params, formats=(fmt,),
                       dpi=0 if dpi is None else dpi, transparent=transparent)
        # 用画布缩放把预览压到上限内（避免超大预览图拖慢界面）
        kind_params = dict(params or {})
        retry = _render_with_scale(kind, target, kind_params, fmt, dpi, transparent, factor)
        if retry is not None and max(retry.size) < max(result.size):
            result = retry
    return result


def _render_with_scale(kind: str, out: Path, params: Dict[str, Any], fmt: str,
                       dpi: Optional[int], transparent: bool, factor: float):
    """按给定缩放系数重绘一次（预览压缩用）；失败返回 None。"""
    ensure_plugins_loaded()
    spec = FIGURES.get(kind)
    if spec is None:
        return None
    clean = spec.prepare(dict(params))
    clean.pop("smiles_info", None)
    try:
        return spec.runner(str(out), formats=(fmt,), dpi=0 if dpi is None else dpi,
                           transparent=transparent, scale_override=factor, **clean)
    except Exception:
        return None


def render(kind: str, out: Optional[str] = None, outdir: Optional[Path] = None,
           params: Optional[Dict[str, Any]] = None, *, formats=None, dpi: Optional[int] = None,
           transparent: Optional[bool] = None) -> FigureResult:
    """出图主入口。

    :param kind:   ``alcohol`` / ``series`` / ``reaction`` / ``chcount``
    :param out:    输出文件路径（可省略，此时用 ``outdir`` + 默认文件名）
    :param params: 参数字典（``None``/空 = 全用默认值）

    :raises RenderError: 图类型未知、参数不合法、SMILES 无法解析
    """
    ensure_plugins_loaded()
    spec = FIGURES.get(kind)
    if spec is None:
        raise RenderError(i18n.t("error.unknown_kind", kind=kind,
                                 available=", ".join(FIGURES)))
    clean = spec.prepare(dict(params or {}))
    path = resolve_output_path(out, kind, outdir)
    info = clean.pop("smiles_info", None)

    # 导出参数：显式参数 > 输出扩展名 > 设置里的默认值
    cfg = settings_mod.Settings.load()
    wanted = list(formats) if formats else None
    if not wanted:
        suffix = Path(out).suffix.lower().lstrip(".") if out else ""
        wanted = [suffix] if suffix in settings_mod.VALID_FORMATS else None
    if not wanted:
        default_fmt = str(cfg.get("defaultFormat") or "png").lower()
        wanted = [default_fmt if default_fmt in settings_mod.VALID_FORMATS else "png"]
    if dpi is None:
        dpi = int(cfg.get("defaultDpi") or 300)
    if transparent is None:
        transparent = bool(cfg.get("transparent"))

    result = spec.runner(path, formats=tuple(wanted), dpi=dpi,
                         transparent=transparent, **clean)

    # 验收 8：300 dpi 时输出最长边应达 ~3000 px。
    # 裁切后的实际内容比画布小，所以量一次再按比例重绘一遍（真放大，非插值）。
    if dpi and "png" in wanted and result.report.outputs.get("png"):
        from . import core as core_mod
        target = core_mod.DPI_TARGET_LONGEST * (float(dpi) / 300.0)
        width, height = result.size
        if max(width, height) < target * 0.98:
            factor = target / float(max(width, height))
            # 第一遍缩放系数约等于 dpi_scale(画布尺寸)；用它把「还差多少像素」
            # 换算成「缩放要再乘多少倍」
            base_scale = core_mod.dpi_scale(width, height, dpi) or 1.0
            retry = spec.runner(path, formats=tuple(wanted), dpi=dpi,
                                transparent=transparent,
                                scale_override=max(0.2, min(12.0, factor * base_scale)),
                                **clean)
            if max(retry.size) > max(result.size):
                result = retry
    if info is not None:
        result.smiles_info = info                      # 供 CLI/GUI 显示
        if getattr(info, "notes", None):
            result.report.warnings.extend(info.notes)
        # 双键的 E/Z 记在骨架里，但用户仍然可以拖原子（审计 F3）。拖过之后
        # "画面看起来的样子"和"导出会写的东西"就分家了 —— 必须说出来，
        # 否则用户以为自己画的是顺式，导出的却是反式。
        conflicts = getattr(getattr(info, "skeleton", None), "stereo_conflicts", None)
        if callable(conflicts):
            result.report.warnings.extend(conflicts())
    return result


# ---------------------------------------------------------------- 内置图（第二批）
def _spec_enthalpy() -> FigureSpec:
    def run(out, title, unit, show_percent, formats=("png",), dpi=None, transparent=False,
            scale_override=None, **_ignored) -> FigureResult:
        return figures.enthalpy_figure(out, title=title or None, unit=unit or "kJ/g",
                                       show_percent=bool(show_percent), formats=formats,
                                       dpi=dpi, transparent=transparent,
                                       scale_override=scale_override)

    return FigureSpec(
        kind="enthalpy",
        title="figure.enthalpy.title",
        description="figure.enthalpy.desc",
        params=(
            ParamSpec("title", "str", "param.title.label", default=""),
            ParamSpec("unit", "str", "param.unit.label", default="kJ/g",
                      help="param.unit.help"),
            ParamSpec("show_percent", "bool", "param.show_percent.label", default=True),
        ),
        default_filename="figure.enthalpy.filename",
        runner=run,
    )


def _spec_esterification() -> FigureSpec:
    def run(out, acid_carbons, alcohol_carbons, title, formats=("png",), dpi=None,
            transparent=False, scale_override=None, **_ignored) -> FigureResult:
        return figures.esterification_figure(out, acid_carbons=acid_carbons,
                                             alcohol_carbons=alcohol_carbons,
                                             title=title or None, formats=formats, dpi=dpi,
                                             transparent=transparent,
                                             scale_override=scale_override)

    return FigureSpec(
        kind="esterification",
        title="figure.esterification.title",
        description="figure.esterification.desc",
        params=(
            ParamSpec("acid_carbons", "int", "param.acid_carbons.label", default=2,
                      minimum=1, maximum=6, help="param.acid_carbons.help"),
            ParamSpec("alcohol_carbons", "int", "param.alcohol_carbons.label", default=1,
                      minimum=1, maximum=6, help="param.alcohol_carbons.help"),
            ParamSpec("title", "str", "param.title.label", default=""),
        ),
        default_filename="figure.esterification.filename",
        runner=run,
    )


def _spec_skeleton() -> FigureSpec:
    def run(out, skeleton, title, rings=False, formats=("png",), dpi=None,
            transparent=False, scale_override=None, **_ignored) -> FigureResult:
        return figures.skeleton_figure(out, skeleton=skeleton or "", title=title or None,
                                       formats=formats, dpi=dpi, transparent=transparent,
                                       scale_override=scale_override,
                                       rings=bool(rings))

    return FigureSpec(
        kind="skeleton",
        title="figure.skeleton.title",
        description="figure.skeleton.desc",
        params=(
            ParamSpec("skeleton", "str", "param.skeleton.label", default="",
                      help="param.skeleton.help"),
            ParamSpec("title", "str", "param.title.label", default=""),
            ParamSpec("rings", "bool", "param.rings.label", default=False,
                      help="param.rings.help"),
        ),
        default_filename="figure.skeleton.filename",
        runner=run,
    )


for _spec in (_spec_enthalpy(), _spec_esterification(), _spec_skeleton()):
    FIGURES[_spec.kind] = _spec

#: 内置图类型（插件不得覆盖；list_figures 也按这个顺序输出）
BUILTIN_KINDS = ("alcohol", "series", "reaction", "chcount", "enthalpy",
                 "esterification", "skeleton")

"""chemfigstudio.cli — 命令行入口。

与旧版单文件脚本 ``chemfig.py`` **完全兼容**：

    python3 chemfig.py alcohol --n 4 --name butan-1-ol --dh -36.1 -o butanol.png
    python3 chemfig.py series   -o 图3_醇系列.png
    python3 chemfig.py reaction -o 图1_丁醇燃烧反应.png
    python3 chemfig.py chcount  -o 图4_丁醇的CH键.png

新增能力：

    python3 -m chemfigstudio.cli alcohol --smiles CCCCO -o butanol.png
    python3 -m chemfigstudio.cli alcohol --smiles CCCCO --json
    python3 -m chemfigstudio.cli doctor          # 环境自检
    python3 -m chemfigstudio.cli gui             # 启动网页 GUI
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import api, core, i18n
from .errors import ConfigError, RenderError
from . import settings as settings_mod

__all__ = ["main", "build_parser", "VERSION"]
VERSION = "0.4.2"


class _CliExit(SystemExit):
    """argparse 想退出时抛这个，由 :func:`main` 转成返回码。

    这样 ``main()`` 永远不会把 SystemExit 抛给调用方（测试 / 嵌入都更安全），
    命令行脚本再用 ``raise SystemExit(main())`` 把码交给 shell。
    """


class _Parser(argparse.ArgumentParser):
    def exit(self, status: int = 0, message: Optional[str] = None):   # noqa: A003
        if message:
            self._print_message(message, sys.stderr)
        raise _CliExit(status)

    def error(self, message: str):                                    # noqa: A003
        self.print_usage(sys.stderr)
        self.exit(2, "%s: error: %s\n" % (self.prog, message))


# --------------------------------------------------------------------- 参数解析

#: 手写了专用子命令的图类型（其余按 api.FIGURES 自动生成）
HAND_WRITTEN_KINDS = ("alcohol", "series", "reaction", "chcount")


def _add_generated_figure_parsers(sub, add_common) -> None:
    """为没有专用子命令的图类型（新内置图 / 插件图）自动生成子命令。"""
    from . import api as api_mod
    api_mod.ensure_plugins_loaded()
    for kind, spec in api_mod.FIGURES.items():
        if kind in HAND_WRITTEN_KINDS:
            continue
        parser = sub.add_parser(kind, help=api_mod.resolve_text(spec.title))
        add_common(parser)
        parser.set_defaults(generated_kind=kind)
        for param in spec.params:
            flag = "--" + param.name.replace("_", "-")
            label = param.label_text()
            if param.type == "int":
                parser.add_argument(flag, dest=param.name, type=int, default=None,
                                    help="%s (int)" % label)
            elif param.type == "bool":
                parser.set_defaults(**{param.name: None})
                parser.add_argument(flag, dest=param.name, action="store_true", default=None,
                                    help=label)
                parser.add_argument("--no-" + param.name.replace("_", "-"), dest=param.name,
                                    action="store_false", help=label)
            elif param.type == "choice" and param.choices:
                parser.add_argument(flag, dest=param.name, default=None,
                                    choices=list(param.choices), help=label)
            else:
                parser.add_argument(flag, dest=param.name, default=None, help=label)


def prescan_language(argv: Sequence[str]) -> Optional[str]:
    """在正式解析前从 argv 里找 ``--lang``（否则 help 文本会先用旧语言生成）。"""
    for index, token in enumerate(argv):
        if token == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--lang="):
            return token.split("=", 1)[1]
    return None


def build_parser() -> argparse.ArgumentParser:
    t = i18n.t
    parser = _Parser(
        prog="chemfigstudio",
        description=t("cli.desc"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=t("cli.epilog"),
    )
    parser.add_argument("--version", action="version", version="chemfigstudio %s" % VERSION)
    parser.add_argument("--lang", default=None, help=t("cli.opt.lang"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-o", "--out", default=None, help=t("cli.opt.out"))
        p.add_argument("--outdir", default=None, help=t("cli.opt.outdir"))
        p.add_argument("--json", action="store_true", help=t("cli.opt.json"))
        p.add_argument("--lang", default=None, help=t("cli.opt.lang"))
        p.add_argument("--format", dest="fmt", default=None,
                       choices=list(settings_mod.VALID_FORMATS), help=t("cli.opt.format"))
        p.add_argument("--dpi", type=int, default=None, help=t("cli.opt.dpi"))
        p.add_argument("--transparent", dest="transparent", action="store_true", default=None,
                       help=t("cli.opt.transparent"))
        p.add_argument("--no-transparent", dest="transparent", action="store_false",
                       help=t("cli.opt.transparent"))

    # alcohol ---------------------------------------------------------------
    a = sub.add_parser("alcohol", help=i18n.t("cli.cmd.alcohol"))
    a.add_argument("--n", "--n-carbons", dest="n_carbons", type=int, default=None,
                   help="碳原子数（乙醇=2 … 己醇=6）")
    a.add_argument("--smiles", default="", help="用 SMILES 指定分子，例如 CCCCO")
    a.add_argument("--name", default="", help="图上的名称/标题")
    a.add_argument("--dh", "--delta-h", dest="delta_h", default=None,
                   help="ΔHc 数值（kJ/g），例如 -36.1")
    a.add_argument("--vertex-labels", "--label-style", dest="label_style",
                   choices=["h2", "ch2"], default="h2",
                   help="转角标注：h2（课本骨架式，默认）或 ch2（直白）")
    a.add_argument("--title", default="", help="自定义标题（默认与 --name 相同）")
    add_common(a)

    # series / reaction / chcount -------------------------------------------
    s = sub.add_parser("series", help=i18n.t("cli.cmd.series"))
    s.add_argument("--title", default="")
    s.add_argument("--include-methanol", dest="include_methanol", action="store_true",
                   help=i18n.t("param.include_methanol.help"))
    s.add_argument("--entries", default="", help=i18n.t("param.entries.help"))
    s.add_argument("--no-dh", action="store_true", help="不显示 ΔHc 列")
    add_common(s)

    r = sub.add_parser("reaction", help=i18n.t("cli.cmd.reaction"))
    r.add_argument("--n", "--n-carbons", dest="n_carbons", type=int, default=4)
    r.add_argument("--dh-mol", dest="dh_mol", type=int, default=None,
                   help="ΔH（kJ/mol）；省略时用内置文献值")
    add_common(r)

    c = sub.add_parser("chcount", help=i18n.t("cli.cmd.chcount"))
    c.add_argument("--n", "--n-carbons", dest="n_carbons", type=int, default=4)
    c.add_argument("--summary", default="", help="底部汇总行（省略时自动生成）")
    add_common(c)

    # 3D 构象导出（不是"图"：出的是 molblock/SDF，由 3D 查看器或报告使用）
    r3 = sub.add_parser("render3d", help=i18n.t("cli.cmd.render3d"))
    r3.add_argument("--smiles", required=True, help=i18n.t("param.smiles.help"))
    r3.add_argument("-o", "--out", default=None, help=i18n.t("cli.opt.out"))
    r3.add_argument("--seed", type=int, default=None,
                    help="随机种子（默认固定值 → 同一分子每次同一构象）")
    r3.add_argument("--conformers", type=int, default=None,
                    help="候选构象数，取 MMFF 能量最低者（默认 8）")
    r3.add_argument("--no-optimise", dest="optimise", action="store_false", default=True,
                    help="跳过 MMFF94 几何优化（更快，几何略糙）")
    r3.add_argument("--json", action="store_true", help=i18n.t("cli.opt.json"))

    # doctor / gui / app ----------------------------------------------------
    sub.add_parser("doctor", help=i18n.t("cli.cmd.doctor"))
    g = sub.add_parser("gui", help=i18n.t("cli.cmd.gui"))
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=8765)
    g.add_argument("--outdir", default=None)
    g.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    g.add_argument("--quiet", action="store_true", help=i18n.t("cli.opt.quiet"))
    sub.add_parser("app", help=i18n.t("cli.cmd.app"))

    _add_generated_figure_parsers(sub, add_common)
    sub.add_parser("plugins", help=i18n.t("cli.cmd.plugins"))

    bt = sub.add_parser("batch", help=i18n.t("cli.cmd.batch"))
    bt.add_argument("csv", help=i18n.t("cli.opt.batch_csv"))
    bt.add_argument("--kind", default="alcohol", choices=list(api.FIGURES),
                    help=i18n.t("cli.opt.batch_kind"))
    bt.add_argument("--outdir", default=None, help=i18n.t("cli.opt.outdir"))
    bt.add_argument("--format", dest="fmt", default=None,
                    choices=list(settings_mod.VALID_FORMATS), help=i18n.t("cli.opt.format"))
    bt.add_argument("--dpi", type=int, default=None, help=i18n.t("cli.opt.dpi"))
    bt.add_argument("--filename-template", default="{index:02d}_{name}",
                    help=i18n.t("cli.opt.batch_template"))
    bt.add_argument("--json", action="store_true", help=i18n.t("cli.opt.json"))
    bt.add_argument("--watch", action="store_true", help=i18n.t("cli.opt.watch"))
    bt.add_argument("--interval", type=float, default=1.0, help=i18n.t("cli.opt.interval"))

    st = sub.add_parser("settings", help=i18n.t("cli.cmd.settings"))
    st.add_argument("--output-dir", default=None, help=i18n.t("cli.opt.set_output_dir"))
    st.add_argument("--set-language", dest="set_language", default=None,
                    choices=["auto", "zh-CN", "en"], help=i18n.t("cli.opt.set_language"))
    st.add_argument("--format", dest="set_format", default=None,
                    choices=list(settings_mod.VALID_FORMATS), help=i18n.t("cli.opt.set_format"))
    st.add_argument("--dpi", dest="set_dpi", type=int, default=None,
                    help=i18n.t("cli.opt.set_dpi"))
    st.add_argument("--transparent", dest="set_transparent", action="store_true", default=None,
                    help=i18n.t("cli.opt.set_transparent"))
    st.add_argument("--no-transparent", dest="set_transparent", action="store_false",
                    help=i18n.t("cli.opt.set_transparent"))
    st.add_argument("--reset", action="store_true", help=i18n.t("cli.opt.set_reset"))
    st.add_argument("--json", action="store_true", help=i18n.t("cli.opt.json"))
    return parser


# ------------------------------------------------------------------------ 输出

def _print_result(result, as_json: bool, stream=None) -> None:
    stream = stream or sys.stdout
    data = result.as_dict()
    if as_json:
        print(json.dumps(data, ensure_ascii=False), file=stream)
        return
    print(i18n.t("cli.done", path=data["path"], size=tuple(data["size"])), file=stream)
    for warning in data["warnings"]:
        print("  " + i18n.t("cli.warn", message=warning), file=stream)


def _cmd_render3d(args: argparse.Namespace) -> int:
    """``render3d``：SMILES → 3D 构象文件（molblock/SDF）。

    出的是**几何数据**而不是位图 —— 3D 的绘制由 3D 查看器（3Dmol.js）负责，
    服务端不自己写渲染（见 docs/DECISIONS.md 的 ADR-012）。
    """
    from . import render3d as render3d_mod

    cfg = settings_mod.Settings.load()
    kwargs: Dict[str, Any] = {}
    if args.seed is not None:
        kwargs["seed"] = int(args.seed)
    if args.conformers is not None:
        kwargs["conformers"] = max(1, int(args.conformers))
    kwargs["optimise"] = bool(args.optimise)

    try:
        conformer = render3d_mod.embed3d(args.smiles, **kwargs)
    except ConfigError as exc:
        print(i18n.t("cli.err.env", message=exc), file=sys.stderr)
        return 3
    except RenderError as exc:
        print(i18n.t("cli.err.param", message=exc), file=sys.stderr)
        return 2

    data = conformer.as_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return 0

    target = Path(args.out).expanduser() if args.out else None
    if target is None:
        base = cfg.output_dir() or Path.cwd()
        target = base / ("%s.sdf" % (conformer.formula or "molecule"))
    if target.is_dir():
        target = target / ("%s.sdf" % (conformer.formula or "molecule"))
    if not target.suffix:
        target = target.with_suffix(".sdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(conformer.molblock, encoding="utf-8")

    print(i18n.t("cli.render3d.wrote") + ": %s" % target)
    print("  " + i18n.t("cli.render3d.summary", formula=conformer.formula or "-",
                        atoms=conformer.atoms, energy=conformer.energy,
                        method=conformer.method))
    for note in conformer.notes:
        print("  " + i18n.t("cli.warn", message=note))
    return 0


def _params_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    """把 CLI 参数映射成 api 的参数字典（只保留该图认识的键）。"""
    if args.cmd == "alcohol":
        return {"smiles": args.smiles, "n_carbons": args.n_carbons, "name": args.name,
                "delta_h": args.delta_h, "label_style": args.label_style,
                "title": args.title}
    if args.cmd == "series":
        return {"title": args.title, "show_delta_h": not args.no_dh,
                "include_methanol": bool(getattr(args, "include_methanol", False)),
                "entries": getattr(args, "entries", "")}
    if args.cmd == "reaction":
        return {"n_carbons": args.n_carbons, "delta_h_kj_per_mol": args.dh_mol}
    if args.cmd == "chcount":
        return {"n_carbons": args.n_carbons, "summary": args.summary}
    raise RenderError("未处理的子命令 %r" % args.cmd)


# --------------------------------------------------------------- 原生应用

def _launch_native_app() -> int:
    """打开 macOS 原生外壳（Swift + WKWebView）；没构建过就先编译。

    非 macOS 平台没有这个外壳 —— 直接用浏览器版（``chemfigstudio gui``）即可，
    功能完全一致（界面就是同一份网页）。
    """
    if sys.platform != "darwin":
        print("原生外壳目前只用于 macOS；请改用浏览器版：chemfigstudio gui",
              file=sys.stderr)
        return 3
    root = Path(__file__).resolve().parents[2]        # …/chemfig-studio
    app = root / "dist" / "ChemFigStudio.app"
    script = root / "scripts" / "build-macos-app.sh"
    if not app.is_dir():
        if not script.exists():
            print("找不到 %s，也无法编译（缺 %s）" % (app, script), file=sys.stderr)
            return 3
        print("首次运行：正在编译原生应用…")
        code = subprocess.call([str(script)])
        if code != 0:
            return code
    return subprocess.call(["open", str(app)])


# ------------------------------------------------------------------- doctor

def _doctor() -> int:
    import platform
    t = i18n.t
    cfg = settings_mod.Settings.load()
    print("chemfigstudio %s" % VERSION)
    print("  %-12s: %s (%s)" % (t("cli.doctor.python"), platform.python_version(), sys.executable))
    try:
        import PIL
        print("  %-12s: %s" % (t("cli.doctor.pillow"), PIL.__version__))
    except Exception as exc:                                  # pragma: no cover
        print("  %-12s: ❌ %s" % (t("cli.doctor.pillow"), exc))
    for entry in core.font_report():
        kind = "bold" if entry["bold"] else "regular"
        if entry.get("ok"):
            print("  %-12s: %s" % (t("cli.doctor.font", kind=kind), entry["path"]))
        else:
            print("  %-12s: ❌ %s" % (t("cli.doctor.font", kind=kind), entry["error"]))
    from . import smiles as smiles_mod
    source = smiles_mod.rdkit_source()
    print("  %-12s: %s" % (t("cli.doctor.rdkit"),
                            ("✅ %s" % source) if source else "—"))
    from . import render3d as render3d_mod
    if render3d_mod.rdkit3d_available():
        print("  %-12s: ✅ %s" % (t("cli.doctor.rdkit3d"), t("cli.doctor.rdkit3d.on")))
    else:
        print("  %-12s: — %s" % (t("cli.doctor.rdkit3d"), t("cli.doctor.rdkit3d.off")))
    print("  %-12s: %s" % (t("cli.doctor.cwd"), Path.cwd()))
    print("  %-12s: %s" % (t("cli.doctor.figures"), ", ".join(api.FIGURES)))
    from . import plugins as plugins_mod
    plugin_info = plugins_mod.plugin_report()
    print("  %-12s: %s" % (t("cli.doctor.plugins"),
                            "%d kinds, %d errors" % (len(plugin_info["kinds"]),
                                                     len(plugin_info["errors"]))))
    print("  %-12s: %s" % (t("cli.doctor.settings"), cfg.path))
    print("  %-12s: %s" % (t("cli.doctor.language"), i18n.current_language()))
    return 0


# ------------------------------------------------------------------------ main

def _handle_settings(args: argparse.Namespace) -> int:
    """查看/修改设置，返回退出码。"""
    t = i18n.t
    cfg = settings_mod.Settings.load()
    changes: Dict[str, Any] = {}
    if args.reset:
        cfg.values = dict(settings_mod.DEFAULTS)
        cfg.extra = {}
        cfg.save()
        if args.json:
            print(json.dumps(cfg.as_dict(), ensure_ascii=False))
        else:
            print(t("cli.settings.reset", path=cfg.path))
        return 0
    if args.output_dir is not None:
        changes["outputDir"] = args.output_dir
    if args.set_language is not None:
        changes["language"] = args.set_language
    if args.set_format is not None:
        changes["defaultFormat"] = args.set_format
    if args.set_dpi is not None:
        changes["defaultDpi"] = args.set_dpi
    if args.set_transparent is not None:
        changes["transparent"] = args.set_transparent

    changed = cfg.update(changes, save=bool(changes))
    if args.json:
        print(json.dumps(cfg.as_dict(), ensure_ascii=False))
        return 0
    if changes and not changed:
        print(t("cli.settings.no_change"))
    elif changed:
        print(t("cli.settings.changed", keys=", ".join(changed)))
    print(t("cli.settings.header", path=cfg.path))
    for key in ("language", "outputDir", "defaultFormat", "defaultDpi", "transparent",
                "pluginsDir"):
        print("  %-14s %s" % (key, cfg.get(key)))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]

    # 先定语言：命令行 > 设置 > auto（这样 --help 也是正确语言）
    cfg = settings_mod.Settings.load()
    i18n.set_language(prescan_language(raw) or cfg.get("language") or "auto")

    parser = build_parser()
    try:
        args = parser.parse_args(raw)
    except _CliExit as exc:                     # --help / --version / 用法错误
        return int(exc.code or 0)

    if getattr(args, "lang", None):
        i18n.set_language(args.lang)

    if args.cmd == "settings":
        try:
            return _handle_settings(args)
        except ConfigError as exc:
            print(i18n.t("cli.err.env", message=exc), file=sys.stderr)
            return 3
        except RenderError as exc:
            print(i18n.t("cli.err.param", message=exc), file=sys.stderr)
            return 2

    if args.cmd == "doctor":
        return _doctor()

    if args.cmd == "render3d":
        return _cmd_render3d(args)

    if getattr(args, "generated_kind", None):
        from . import api as api_mod
        spec = api_mod.FIGURES.get(args.generated_kind)
        if spec is None:
            print(i18n.t("cli.err.param", message="unknown kind: %s" % args.generated_kind),
                  file=sys.stderr)
            return 2
        params = {param.name: getattr(args, param.name)
                  for param in spec.params
                  if getattr(args, param.name, None) is not None}
        try:
            result = api.render(args.generated_kind, out=args.out,
                                outdir=(Path(args.outdir) if args.outdir
                                        else (cfg.output_dir() or None)),
                                params=params,
                                formats=[args.fmt] if getattr(args, "fmt", None) else None,
                                dpi=getattr(args, "dpi", None),
                                transparent=getattr(args, "transparent", None))
        except ConfigError as exc:
            print(i18n.t("cli.err.env", message=exc), file=sys.stderr)
            return 3
        except RenderError as exc:
            print(i18n.t("cli.err.param", message=exc), file=sys.stderr)
            return 2
        _print_result(result, args.json)
        return 0

    if args.cmd == "plugins":
        from . import plugins as plugins_mod
        info = plugins_mod.plugin_report()          # 名字别叫 report：下面 batch 分支同名会串类型
        if getattr(args, "json", False):
            print(json.dumps(info, ensure_ascii=False))
            return 0
        print(i18n.t("cli.plugins.header"))
        for directory in info["dirs"]:
            print("  " + directory)
        if not info["plugins"]:
            print("  " + i18n.t("cli.plugins.none"))
        for item in info["plugins"]:
            mark = "✅" if item["ok"] else "❌"
            kinds = ", ".join(item["kinds"]) or "-"
            print("  %s %-28s %s" % (mark, item["name"], kinds))
            if not item["ok"]:
                print("      " + item["error"])
        print(i18n.t("cli.plugins.summary", count=len(info["kinds"]),
                     errors=len(info["errors"])))
        return 0

    if args.cmd == "batch":
        from . import batch as batch_mod

        if getattr(args, "watch", False):
            target = Path(args.outdir) if args.outdir else (cfg.output_dir() or Path.cwd())
            interval = max(0.05, float(getattr(args, "interval", 1.0) or 1.0))

            def announce(event):
                if event.get("error"):
                    print("  ⚠ %s" % event["error"], file=sys.stderr)
                else:
                    print(i18n.t("cli.watch.rendered", succeeded=event["succeeded"],
                                 failed=event["failed"], path=target))

            try:
                print(i18n.t("cli.watch.watching", path=args.csv, interval=interval))
                batch_mod.watch(args.csv, target, kind=args.kind,
                                formats=(args.fmt or cfg.get("defaultFormat") or "png",),
                                dpi=args.dpi or int(cfg.get("defaultDpi") or 300),
                                interval=interval,
                                filename_template=args.filename_template,
                                on_event=announce)
            except KeyboardInterrupt:
                print(i18n.t("cli.watch.stopped"))
                return 0
            return 0

        try:
            items = batch_mod.read_csv(args.csv)
            target = Path(args.outdir) if args.outdir else (cfg.output_dir() or Path.cwd())
            fmt = args.fmt or cfg.get("defaultFormat") or "png"
            progress = None if args.json else (
                lambda index, total, item: print(i18n.t("cli.batch.progress", index=index,
                                                        total=total, name=item.name or item.smiles)))
            report = batch_mod.run_batch(items, target, kind=args.kind, formats=(fmt,),
                                         dpi=args.dpi or int(cfg.get("defaultDpi") or 300),
                                         filename_template=args.filename_template,
                                         on_progress=progress)
        except ConfigError as exc:
            print(i18n.t("cli.err.env", message=exc), file=sys.stderr)
            return 3
        except RenderError as exc:
            print(i18n.t("cli.err.param", message=exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False))
        else:
            for item in report.items:
                if item.get("error"):
                    print("  ❌ %s" % i18n.t("cli.batch.item_failed",
                                            name=item["name"], message=item["error"]))
                else:
                    print("  ✅ %s → %s" % (item["name"], item["path"]))
            for warning in report.warnings:
                print("  " + i18n.t("cli.warn", message=warning))
            print(i18n.t("cli.batch.summary", succeeded=len(report.succeeded),
                         failed=len(report.failed), total=report.total))
        return 0 if not report.failed else 2

    if args.cmd == "gui":
        from .gui.server import serve
        return serve(host=args.host, port=args.port, outdir=args.outdir,
                     open_browser=not args.no_browser,
                     quiet=getattr(args, "quiet", False))

    if args.cmd == "app":
        return _launch_native_app()

    # 输出目录优先级：命令行 > 设置 > 当前目录
    if args.outdir:
        outdir = Path(args.outdir)
    elif cfg.get("outputDir"):
        outdir = cfg.output_dir()
    else:
        outdir = None
    try:
        params = {k: v for k, v in _params_from_args(args).items() if v is not None}
        result = api.render(args.cmd, out=args.out, outdir=outdir, params=params,
                            formats=[args.fmt] if getattr(args, "fmt", None) else None,
                            dpi=getattr(args, "dpi", None),
                            transparent=getattr(args, "transparent", None))
    except RenderError as exc:
        print(i18n.t("cli.err.param", message=exc), file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(i18n.t("cli.err.env", message=exc), file=sys.stderr)
        return 3
    _print_result(result, as_json=getattr(args, "json", False))
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())

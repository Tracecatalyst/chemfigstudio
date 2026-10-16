"""chemfigstudio 的本地网页界面（GUI）。

设计取舍见 docs/DECISIONS.md ADR-001：用 stdlib ``http.server`` 而不是
tkinter / PyQt —— 零依赖、可被 curl 端到端测试、离线可用、跨平台。

对外只有 :func:`serve` 一个入口；HTTP 处理逻辑在 :class:`GuiHandler`。
"""

from __future__ import annotations

import json
import mimetypes
import socket
import subprocess
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from .. import __version__, api, batch as batch_mod, document as document_mod, export as export_mod, i18n
from ..history import DocumentSession
from .. import settings as settings_mod
from ..errors import ConfigError, RenderError

__all__ = ["serve", "make_server", "GuiHandler"]

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 1 << 20          # 1 MiB，够用且防滥用


# --------------------------------------------------------------------- 工具

def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _safe_child(base: Path, name: str, *, allow_dirs: bool = False) -> Optional[Path]:
    """把 ``name`` 限制在 ``base`` 目录内（防目录穿越），返回真实路径。

    ``allow_dirs=True`` 时允许 ``vendor/3Dmol-min.js`` 这类**子目录**静态资源；
    仍然逐段拒绝 ``..``、隐藏文件，且最终必须落在 ``base`` 内。
    """
    if not name or "\\" in name or name.startswith("."):
        return None
    parts = [part for part in name.split("/") if part]
    if not allow_dirs and len(parts) != 1:
        return None
    if not parts or any(part == ".." or part.startswith(".") for part in parts):
        return None
    candidate = base.joinpath(*parts).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate


def _path_within(candidate: Path, root: Path) -> bool:
    """``candidate`` 是否落在 ``root`` 内（已解析真实路径，防符号链接绕过）。"""
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _origin_allowed(origin: str, host_header: str) -> bool:
    """同源判定：``Origin`` 的主机必须是**回环地址**，且与 ``Host`` 一致。

    为什么需要它：服务只监听回环，但用户浏览器里任何一个网页都能向
    ``127.0.0.1:8765`` 发跨站 POST（简单请求不触发预检）。没有这道检查时，
    恶意页面可以借用户之手改设置、写文件、读本地 CSV。
    非浏览器客户端（curl/脚本）不带 ``Origin``，照常放行。
    """
    from urllib.parse import urlsplit
    parts = urlsplit(origin)
    host = (parts.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
        return False
    if host_header:
        return parts.netloc.lower() == host_header.lower()
    return True


class GuiHandler(BaseHTTPRequestHandler):
    """HTTP 处理器；类属性 ``outdir`` / ``verbose`` 由 :func:`make_server` 注入。"""

    server_version = "chemfigstudio/%s" % __version__
    outdir: Path = Path.cwd()
    #: GUI/批量允许写盘的根目录（默认只有 outdir；settings.allowedWriteDirs 可加）
    write_roots: Tuple[Path, ...] = ()
    #: 实际绑定地址与端口（由 make_server 注入，用于 Origin 同源判定）
    bound_host: str = "127.0.0.1"
    bound_port: int = 0
    verbose: bool = False
    settings: Optional["settings_mod.Settings"] = None            # 由 make_server 注入
    session: Optional["DocumentSession"] = None                   # 编辑会话（多标签 + 历史）

    @property
    def cfg(self) -> "settings_mod.Settings":
        """非 Optional 的设置访问（没注入时按需加载）。"""
        current = self.settings
        if current is None:
            current = settings_mod.Settings.load()
            type(self).settings = current
        return current

    # ------------------------------------------------------------- 基础响应
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any) -> None:
        self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json(404, {"ok": False, "error": "文件不存在：%s" % path.name})
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype)

    def log_message(self, fmt: str, *args: Any) -> None:      # noqa: A003
        if self.verbose:
            sys.stderr.write("[gui] %s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:                                  # noqa: N802
        route = urlparse(self.path).path

        if route in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html")
            return
        if route in ("/viewer3d", "/viewer3d.html"):
            # 3D 查看器是独立页面（3Dmol.js ~0.5 MB）：主界面不需要它，
            # 按需加载，避免拖慢启动。
            self._send_file(STATIC_DIR / "viewer3d.html")
            return
        if route.startswith("/static/"):
            # 允许子目录：3Dmol.js 这类第三方库放在 static/vendor/ 下
            child = _safe_child(STATIC_DIR, unquote(route[len("/static/"):]),
                                allow_dirs=True)
            if child is None:
                self._send_json(403, {"ok": False, "error": "非法静态资源路径"})
            else:
                self._send_file(child)
            return
        if route.startswith("/preview/"):
            child = _safe_child(api.preview_dir(), unquote(route[len("/preview/"):]))
            if child is None:
                self._send_json(403, {"ok": False, "error": "非法预览路径"})
            else:
                self._send_file(child)
            return
        if route == "/api/documents":
            self._send_json(200, {"ok": True, "drafts": document_mod.list_drafts(),
                                  "draftsDir": str(document_mod.drafts_dir())})
            return
        if route.startswith("/api/documents/"):
            name = unquote(route[len("/api/documents/"):])
            try:
                doc = document_mod.load_draft(name)
            except RenderError as exc:
                self._send_json(404, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, {"ok": True, "document": doc.as_dict(), "id": Path(name).name})
            return
        if route.startswith("/out/"):
            child = _safe_child(self.outdir, unquote(route[len("/out/"):]))
            if child is None:
                self._send_json(403, {"ok": False, "error": "非法输出路径"})
            else:
                self._send_file(child)
            return
        if route == "/api/figures":
            lang = self._query_lang()
            self._send_json(200, {"ok": True, "figures": api.list_figures(lang),
                                  "language": i18n.resolve_language(lang),
                                  "outdir": str(self.outdir)})
            return
        if route == "/api/settings":
            self._send_json(200, {"ok": True, "settings": self.cfg.as_dict(),
                                  "languages": i18n.available_languages()})
            return
        if route == "/api/plugins":
            from .. import plugins as plugins_mod
            self._send_json(200, {"ok": True, "plugins": plugins_mod.plugin_report()})
            return
        if route == "/api/session":
            session = self._session()
            active = session.active
            # 与 POST 保持同构：GET 也要给当前文档（客户端靠它读参数，含骨架 JSON）
            self._send_json(200, {"ok": True, "session": session.as_dict(),
                                  "document": active.document.as_dict() if active else None,
                                  "activeId": session.active_id})
            return
        if route.startswith("/api/i18n/"):
            wanted = unquote(route[len("/api/i18n/"):]) or "auto"
            resolved = i18n.resolve_language(wanted)
            self._send_json(200, {"ok": True, "language": resolved,
                                  "catalog": i18n.catalog(resolved),
                                  "languages": i18n.available_languages()})
            return
        if route == "/api/health":
            self._send_json(200, {"ok": True, "version": __version__,
                                  "outdir": str(self.outdir),
                                  "language": i18n.current_language(),
                                  "figures": sorted(api.FIGURES)})
            return
        self._send_json(404, {"ok": False, "error": "未知路径 %s" % route})

    # ------------------------------------------------------------------ PUT
    def do_PUT(self) -> None:                                  # noqa: N802
        self.do_POST()

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:                                 # noqa: N802
        route = urlparse(self.path).path
        if not self._check_request_origin():
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if route == "/api/render":
            self._handle_render(payload)
        elif route == "/api/reveal":
            self._handle_reveal(payload)
        elif route == "/api/settings":
            self._handle_settings(payload)
        elif route == "/api/export":
            self._handle_export(payload)
        elif route == "/api/preview":
            self._handle_preview(payload)
        elif route == "/api/documents":
            self._handle_save_document(payload)
        elif route == "/api/session":
            self._handle_session(payload)
        elif route == "/api/batch":
            self._handle_batch(payload)
        elif route == "/api/render3d":
            self._handle_render3d(payload)
        else:
            self._send_json(404, {"ok": False, "error": "未知路径 %s" % route})

    # -------------------------------------------------------------- 边界检查
    def _write_roots(self) -> "Tuple[Path, ...]":
        """允许写盘的目录集合：``outdir`` + 设置里的 ``allowedWriteDirs``。"""
        roots = [self.outdir]
        for entry in (self.cfg.get("allowedWriteDirs") or []):
            roots.append(Path(str(entry)).expanduser())
        return tuple(roots)

    def _resolve_write_dir(self, requested: Optional[str]) -> Optional[Path]:
        """把请求里的 ``dir`` 收口到允许写盘目录内；越界返回 ``None``。

        此前 ``dir`` 直通 ``export_mod``，任何网页都能请求把文件写到文件系统任意位置
        （已复现）。现在默认只能写 ``outdir``，要写别处必须在设置面板里显式加白名单
        —— 那是用户自己的动作，不是第三方页面的。
        """
        roots = self._write_roots()
        if not requested:
            return roots[0]
        candidate = Path(str(requested)).expanduser()
        for root in roots:
            if _path_within(candidate, root):
                return candidate
        return None

    def _check_request_origin(self) -> bool:
        """校验 Origin/Host/Content-Type；不合法时回 403 并返回 False。"""
        from urllib.parse import urlsplit
        origin = self.headers.get("Origin")
        host_header = self.headers.get("Host") or ""
        if origin and not _origin_allowed(origin, host_header):
            self._send_json(403, {"ok": False, "error": "跨站请求被拒绝（Origin 不同源）"})
            return False
        # Host 头回环校验：DNS rebinding 会让主机名指向 127.0.0.1 但 Host 是外域名
        if host_header:
            host_name = (urlsplit("//" + host_header).hostname or "").lower()
            if self.bound_host in ("127.0.0.1", "localhost", "::1"):
                if host_name not in ("127.0.0.1", "localhost", "::1", ""):
                    self._send_json(403, {"ok": False,
                                          "error": "Host 不是回环地址，已拒绝"})
                    return False
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype not in ("application/json", "text/json"):
            # 表单/纯文本跨站简单请求会走到这里（它们无法带 application/json）
            self._send_json(403, {"ok": False,
                                  "error": "请求必须是 application/json"})
            return False
        return True

    # -------------------------------------------------------------- 具体接口
    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("请求体过大（%d 字节）" % length)
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求体不是合法 JSON：%s" % exc)
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _handle_render(self, payload: Dict[str, Any]) -> None:
        kind = str(payload.get("kind") or "")
        params = payload.get("params") or {}
        filename = payload.get("filename") or None
        if not isinstance(params, dict):
            self._send_json(400, {"ok": False, "error": "params 必须是对象"})
            return
        out: Optional[str] = None
        if filename:
            child = _safe_child(self.outdir, str(filename))
            if child is None:
                self._send_json(400, {"ok": False, "error": "文件名不合法（不能含路径）"})
                return
            out = str(child)
        try:
            result = api.render(kind, out=out, outdir=self.outdir, params=params)
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:                              # pragma: no cover
            self.log_message("render failed: %s", traceback.format_exc())
            self._send_json(500, {"ok": False, "error": "内部错误：%s" % exc})
            return

        data = result.as_dict()
        data["ok"] = True
        data["file"] = Path(str(data["path"])).name
        data["url"] = "/out/%s" % Path(str(data["path"])).name
        self._send_json(200, data)

    def _handle_render3d(self, payload: Dict[str, Any]) -> None:
        """SMILES → 3D 构象（molblock）。只回几何数据，渲染在前端。

        成功：``{"ok":true, …Conformer3D.as_dict()}``；
        RDKit 缺失 → **503** + 安装指引（环境能力缺失，不是用户写错）；
        SMILES 非法 → **400**。
        """
        from .. import render3d as render3d_mod

        raw = payload.get("smiles")
        smiles = str(raw).strip() if raw is not None else ""
        if not smiles:
            self._send_json(400, {"ok": False, "error": "smiles 不能为空"})
            return

        kwargs: Dict[str, Any] = {}
        if payload.get("seed") is not None:
            try:
                kwargs["seed"] = int(payload["seed"])
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": "seed 必须是整数"})
                return
        if payload.get("conformers") is not None:
            try:
                kwargs["conformers"] = max(1, int(payload["conformers"]))
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": "conformers 必须是整数"})
                return
        if payload.get("optimise") is not None:
            kwargs["optimise"] = bool(payload["optimise"])

        try:
            conformer = render3d_mod.embed3d(smiles, **kwargs)
        except ConfigError as exc:
            self._send_json(503, {"ok": False, "error": str(exc), "needRdkit": True})
            return
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:                              # pragma: no cover
            self.log_message("render3d failed: %s", traceback.format_exc())
            self._send_json(500, {"ok": False, "error": "内部错误：%s" % exc})
            return

        data = conformer.as_dict()
        data["ok"] = True
        self._send_json(200, data)

    def _query_lang(self) -> Optional[str]:
        """从 query string 取 lang（``?lang=en``）；没有就返回 None（= 用当前语言）。"""
        query = urlparse(self.path).query
        for part in query.split("&"):
            if part.startswith("lang="):
                return unquote(part[5:]) or None
        return None

    def _handle_settings(self, payload: Dict[str, Any]) -> None:
        """读取（GET 已处理）与更新设置；body 形如 ``{"changes": {...}}`` 或平铺字段。"""
        changes = payload.get("changes")
        if changes is None:
            changes = {k: v for k, v in payload.items() if not k.startswith("_")}
        if not isinstance(changes, dict):
            self._send_json(400, {"ok": False, "error": "changes 必须是对象"})
            return
        try:
            changed = self.cfg.update(changes, save=True)
        except ConfigError as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})
            return
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if "language" in changed:
            i18n.set_language(self.cfg.get("language"))
        data = self.cfg.as_dict()
        self._send_json(200, {"ok": True, "changed": changed, "settings": data,
                              "languages": i18n.available_languages(),
                              "language": i18n.current_language()})

    @classmethod
    def _session(cls) -> DocumentSession:
        """懒建会话（一个服务进程一份）。"""
        session = cls.session
        if session is None:
            session = DocumentSession()
            session.new_tab(document_mod.Document.new("alcohol", {}))
            cls.session = session
        return session

    def _handle_session(self, payload: Dict[str, Any]) -> None:
        """会话操作：newTab / closeTab / select / apply / undo / redo / addLayer / toggleLayer。

        所有操作**只改内存中的文档，不落盘**（/2）；导出仍走 /api/export。
        """
        session = self._session()
        action = str(payload.get("action") or "")
        tab_id = payload.get("tabId") or None
        try:
            if action == "newTab":
                # 没给名字就自动编号（否则所有标签都显示成图类型，用户看不出区别）
                title = str(payload.get("title") or "").strip()
                if not title:
                    title = i18n.t("gui.tab.untitled", index=len(session.tabs) + 1)
                tab_id = session.new_tab(
                    document_mod.Document.from_dict(payload.get("document") or
                                                    {"mode": "figure",
                                                     "figure": {"kind": "alcohol", "params": {}}}),
                    title=title)
            elif action == "closeTab":
                session.close_tab(tab_id or "")
            elif action == "select":
                session.select(tab_id or "")
            elif action == "renameTab":
                session.rename_tab(tab_id or "", str(payload.get("title") or ""))
            elif action == "apply":
                session.set_figure(str(payload.get("kind") or "alcohol"),
                                   payload.get("params") or {}, tab_id=tab_id)
            elif action == "undo":
                session.undo(tab_id)
            elif action == "redo":
                session.redo(tab_id)
            elif action == "addLayer":
                session.add_layer(str(payload.get("name") or "layer"), tab_id=tab_id)
            elif action == "toggleLayer":
                session.toggle_layer(str(payload.get("layerId") or ""),
                                     payload.get("visible"), tab_id=tab_id)
            else:
                self._send_json(400, {"ok": False, "error": "unknown action: %s" % action})
                return
        except (ValueError, RenderError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        active = session.active
        self._send_json(200, {"ok": True, "session": session.as_dict(),
                              "document": active.document.as_dict() if active else None,
                              "activeId": session.active_id})

    def _handle_batch(self, payload: Dict[str, Any]) -> None:
        """批量导出：``dryRun`` 只解析并回报会写哪些文件（含已存在的），不落盘。"""
        cfg = self.cfg
        try:
            if payload.get("csv"):
                items = batch_mod.read_csv_text(str(payload["csv"]))
            elif payload.get("path"):
                items = batch_mod.read_csv(payload["path"])
            else:
                self._send_json(400, {"ok": False,
                                      "error": i18n.t("batch.err.missing_column",
                                                      column="csv", columns="-")})
                return
            resolved = self._resolve_write_dir(payload.get("dir"))
            if resolved is None:
                self._send_json(403, {"ok": False,
                                      "error": i18n.t("export.err.dir_not_allowed",
                                                      dirs=", ".join(
                                                          str(r) for r in self._write_roots()))})
                return
            directory = str(resolved)
            fmt = str(payload.get("format") or cfg.get("defaultFormat") or "png")
            template = str(payload.get("filenameTemplate") or "{index:02d}_{name}")
            # kind 必须在算待写文件之前解析：run_batch 用它作为 fallback，
            # 两边不一致就会"确认了 A、实际覆盖 B"（数据丢失级缺陷）。
            kind = str(payload.get("kind") or "alcohol")
            targets = batch_mod.target_paths(items, directory, kind=kind, formats=(fmt,),
                                            filename_template=template)
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        existing = [str(path) for path in targets if path.exists()]
        rows = [item.as_dict() for item in items]
        if payload.get("dryRun"):
            self._send_json(200, {"ok": True, "dryRun": True, "rows": rows,
                                  "count": len(items), "targets": [str(p) for p in targets],
                                  "existing": existing, "dir": str(directory)})
            return
        if existing and not payload.get("overwrite"):
            self._send_json(200, {"ok": False, "needs_confirmation": True,
                                  "existing": existing, "rows": rows, "count": len(items),
                                  "message": i18n.t("export.exists_warning")})
            return

        dpi = int(str(payload.get("dpi") or cfg.get("defaultDpi") or 300))
        try:
            report = batch_mod.run_batch(items, directory, kind=kind, formats=(fmt,),
                                         dpi=dpi, filename_template=template)
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        data = report.as_dict()
        data.update({"ok": True, "dir": str(directory)})
        self._send_json(200, data)

    def _handle_preview(self, payload: Dict[str, Any]) -> None:
        """画布预览：不写用户输出目录（/2），1:1 绘制并封顶 1200 px。"""
        kind = str(payload.get("kind") or "")
        try:
            # dpi 缺省由 api.preview 决定（0 = 1:1 绘制）；传 150 反而会把预览缩小
            raw_dpi = payload.get("dpi")
            result = api.preview(kind, payload.get("params") or {},
                                 dpi=None if raw_dpi in (None, "") else int(str(raw_dpi)),
                                 transparent=bool(payload.get("transparent")),
                                 svg=bool(payload.get("svg")))
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        data = result.as_dict()
        name = Path(str(data["path"])).name
        data.update({"ok": True, "file": name, "url": "/preview/" + name})
        self._send_json(200, data)

    def _handle_save_document(self, payload: Dict[str, Any]) -> None:
        """存草稿（到配置目录的 drafts/，不碰输出目录）。"""
        try:
            doc = document_mod.Document.from_dict(payload.get("document") or payload)
            path = doc.save_draft()
        except (RenderError, ConfigError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "id": path.name, "path": str(path),
                              "drafts": document_mod.list_drafts()})

    def _handle_export(self, payload: Dict[str, Any]) -> None:
        """导出：先把"会写哪些文件/是否已存在"算出来，需要确认就交回前端。"""
        cfg = self.cfg
        directory = self._resolve_write_dir(payload.get("dir"))
        if directory is None:
            self._send_json(403, {"ok": False,
                                  "error": i18n.t("export.err.dir_not_allowed",
                                                  dirs=", ".join(
                                                      str(r) for r in self._write_roots()))})
            return
        request = export_mod.ExportRequest(
            kind=str(payload.get("kind") or ""),
            params=payload.get("params") or {},
            directory=str(directory),
            filename=str(payload.get("filename") or ""),
            format=str(payload.get("format") or cfg.get("defaultFormat") or "png"),
            dpi=int(payload.get("dpi") or cfg.get("defaultDpi") or 300),
            transparent=bool(payload.get("transparent", cfg.get("transparent"))),
            overwrite=bool(payload.get("overwrite")),
        )
        try:
            prepared = export_mod.plan(request)
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if prepared.needs_confirmation:
            data = prepared.as_dict()
            data.update({"ok": False, "needs_confirmation": True,
                         "message": i18n.t("export.exists_warning")})
            self._send_json(200, data)
            return
        try:
            result = export_mod.run(request)
        except RenderError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:                                  # pragma: no cover
            self.log_message("export failed: %s", traceback.format_exc())
            self._send_json(500, {"ok": False, "error": "内部错误：%s" % exc})
            return
        data = result.as_dict()
        data["ok"] = True
        main = Path(data["path"])
        data["file"] = main.name
        data["url"] = "/out/%s" % main.name
        self._send_json(200, data)

    def _handle_reveal(self, payload: Dict[str, Any]) -> None:
        name = str(payload.get("file") or "")
        child = _safe_child(self.outdir, name)
        if child is None or not child.exists():
            self._send_json(404, {"ok": False, "error": "文件不存在"})
            return
        try:                                                  # macOS: 在 Finder 中显示
            subprocess.run(["open", "-R", str(child)], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._send_json(200, {"ok": True})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})


def start_parent_watchdog(parent_pid: int, interval: float = 1.0) -> None:
    """父进程（原生外壳）消失后自动退出，避免留下孤儿服务进程。

    macOS/Linux 上父进程退出后本进程会被 reparent 到 pid 1，
    所以只要 ``os.getppid()`` 变了就说明外壳没了 —— 即使外壳被 SIGKILL 也能收尸。
    """
    import os
    import signal
    import threading
    import time

    def watch() -> None:
        while True:
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(interval)

    threading.Thread(target=watch, daemon=True).start()


def make_server(host: str = "127.0.0.1", port: int = 8765,
                outdir: Optional[Path] = None, verbose: bool = False,
                tries: int = 12, cfg: Optional["settings_mod.Settings"] = None,
                ) -> Tuple[ThreadingHTTPServer, int]:
    """创建服务器（端口被占用时自动 +1 试到 ``tries`` 次）。

    ``port=0`` 表示交给操作系统分配空闲端口（测试用），此时只尝试一次。
    """
    cfg = cfg or settings_mod.Settings.load()
    i18n.set_language(cfg.get("language"))
    directory = Path(outdir).expanduser() if outdir else cfg.output_dir()
    directory.mkdir(parents=True, exist_ok=True)

    handler = type("BoundGuiHandler", (GuiHandler,),
                   {"outdir": directory, "verbose": verbose, "settings": cfg,
                    "bound_host": host})
    last_error: Optional[OSError] = None
    attempts = 1 if port == 0 else tries
    for offset in range(attempts):
        candidate = port + offset
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            httpd.daemon_threads = True
            # 实际端口回填给 handler：Origin 同源判定要用它
            # （handler 是 type(...) 动态生成的类，mypy 看不到这个属性 → setattr）
            setattr(handler, "bound_port", httpd.server_address[1])
            return httpd, httpd.server_address[1]
        except OSError as exc:
            last_error = exc
            continue
    raise OSError("端口 %d-%d 都被占用：%s" % (port, port + tries - 1, last_error))


def serve(host: str = "127.0.0.1", port: int = 8765, outdir: Optional[str] = None,
          open_browser: bool = True, verbose: bool = False, quiet: bool = False) -> int:
    """启动 GUI（阻塞），返回进程退出码。Ctrl-C 退出。"""
    cfg = settings_mod.Settings.load()
    httpd, actual_port = make_server(host=host, port=port,
                                     outdir=Path(outdir) if outdir else None,
                                     verbose=verbose, cfg=cfg)
    url = "http://%s:%d/" % (host, actual_port)
    directory = Path(outdir).expanduser() if outdir else Path.cwd()
    if not quiet:
        print("chemfigstudio GUI 已启动：%s" % url)
        print("  输出目录：%s" % directory)
        print("  Ctrl-C 结束")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="chemfigstudio 本地 GUI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="打印每个 HTTP 请求")
    ap.add_argument("--quiet", action="store_true", help="不打印启动横幅（原生外壳用）")
    ap.add_argument("--parent-pid", type=int, default=None,
                    help="父进程 PID：它退出后本服务自动结束（原生外壳用）")
    ns = ap.parse_args()
    if ns.parent_pid:
        start_parent_watchdog(ns.parent_pid)
    raise SystemExit(serve(host=ns.host, port=ns.port, outdir=ns.outdir,
                           open_browser=not ns.no_browser, verbose=ns.verbose,
                           quiet=ns.quiet))

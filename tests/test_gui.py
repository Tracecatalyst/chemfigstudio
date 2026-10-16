"""GUI 端到端测试：真的起一个 HTTP 服务，用 urllib 打完整个流程。

不依赖浏览器，因此可以在 CI / 无界面环境跑。
"""

from __future__ import annotations

import _bootstrap  # noqa: F401  （测试隔离：见 tests/_bootstrap.py）
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from chemfigstudio import settings as settings_mod
from chemfigstudio.gui import server as gui_server


class GuiHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.outdir = Path(cls._tmp.name)
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.outdir)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    # ------------------------------------------------------------- helpers
    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as res:
            return res.status, res.read()

    def post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # --------------------------------------------------------------- tests
    def test_index_and_static_assets(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"chemfigstudio", body)
        for asset in ("/static/app.js", "/static/style.css"):
            status, body = self.get(asset)
            self.assertEqual(status, 200, asset)
            self.assertTrue(body)

    def test_frontend_wires_i18n_and_settings(self):
        """前端契约：index.html 用 data-i18n 标注、app.js 调 i18n/settings 接口。"""
        _status, html = self.get("/")
        _status, js = self.get("/static/app.js")
        html, js = html.decode("utf-8"), js.decode("utf-8")
        self.assertGreaterEqual(html.count("data-i18n="), 12)     # 界面文案全部可翻译
        for hook in ("/api/i18n/", "/api/settings", "/api/figures?lang=", "choice_labels"):
            self.assertIn(hook, js, "app.js 缺少 %s" % hook)
        for hook in ("chemfigApplyText", "chemfigCopyImage", "windowState", "installDragAndDrop",
                     "renameTab", "startRenameTab", "tab-input"):
            self.assertIn(hook, js, "app.js 缺少外壳增强：%s" % hook)
        _status, css = self.get("/static/style.css")
        css = css.decode("utf-8")
        # hidden 属性必须能压过 .sheet 的 display:flex（否则弹层一直罩住界面 —— 已踩过）
        self.assertIn(".sheet[hidden]", css)

    def test_health(self):
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertIn("alcohol", payload["figures"])

    def test_figures_spec_is_gui_ready(self):
        status, body = self.get("/api/figures")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        kinds = [f["kind"] for f in payload["figures"]]
        self.assertEqual(kinds, ["alcohol", "series", "reaction", "chcount",
                                 "enthalpy", "esterification", "skeleton"])
        for fig in payload["figures"]:
            self.assertTrue(fig["title"])
            for param in fig["params"]:
                self.assertIn(param["type"], ("int", "str", "choice", "bool", "smiles"))
                self.assertTrue(param["label"])

    def test_render_roundtrip_and_preview(self):
        status, payload = self.post("/api/render", {
            "kind": "alcohol",
            "params": {"n_carbons": 3, "name": "propan-1-ol"},
            "filename": "gui_test.png",
        })
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["file"], "gui_test.png")
        self.assertEqual(payload["warnings"], [])
        self.assertTrue((self.outdir / "gui_test.png").exists())

        status, body = self.get(payload["url"])
        self.assertEqual(status, 200)
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")

    def test_render_with_smiles(self):
        status, payload = self.post("/api/render", {
            "kind": "alcohol", "params": {"smiles": "CCCCO"},
            "filename": "gui_smiles.png",
        })
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["smiles"]["n_carbons"], 4)

    def test_render_series(self):
        status, payload = self.post("/api/render", {
            "kind": "series", "params": {}, "filename": "gui_series.png"})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["size"][1] > 600)

    def test_bad_kind_returns_400(self):
        status, payload = self.post("/api/render", {"kind": "nope", "params": {}})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_bad_param_returns_400(self):
        status, payload = self.post("/api/render", {
            "kind": "alcohol", "params": {"n_carbons": 99}})
        self.assertEqual(status, 400)

    def test_path_traversal_is_blocked(self):
        status, payload = self.post("/api/render", {
            "kind": "alcohol", "params": {"n_carbons": 2},
            "filename": "../escaped.png"})
        self.assertEqual(status, 400)
        self.assertFalse((self.outdir.parent / "escaped.png").exists())

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/out/../server.py")
        self.assertEqual(ctx.exception.code, 403)

    def test_unknown_route_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/nope")
        self.assertEqual(ctx.exception.code, 404)


CJK = range(0x4E00, 0x9FFF)          # 汉字区间； 用它扫"残留中文"


def has_cjk(text: str) -> bool:
    return any(ord(ch) in CJK for ch in text or "")


class GuiSettingsAndI18nTests(unittest.TestCase):
    """设置接口 / i18n 接口 / 英文模式无残留中文（、16）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.outdir = cls.root / "out"
        cls._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(cls.root / "cfg")
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.outdir)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = cls._old_cfg
        cls._tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def put(self, path, payload):
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="PUT")
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def test_settings_roundtrip(self):
        status, payload = self.get("/api/settings")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("languages", payload)
        self.assertTrue(str(payload["settings"]["_path"]).endswith("settings.json"))

        status, payload = self.put("/api/settings",
                                   {"changes": {"defaultFormat": "svg", "defaultDpi": 450,
                                                "language": "en"}})
        self.assertEqual(status, 200)
        self.assertIn("defaultFormat", payload["changed"])
        self.assertEqual(payload["settings"]["defaultFormat"], "svg")
        self.assertEqual(payload["language"], "en")

        # 落盘了：新开一个 Settings 能读到
        again = settings_mod.Settings.load()
        self.assertEqual(again.get("defaultFormat"), "svg")
        self.assertEqual(again.get("defaultDpi"), 450)

        # 还原语言，避免影响其它用例
        self.put("/api/settings", {"changes": {"language": "auto"}})

    def test_invalid_settings_change_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.put("/api/settings", {"changes": "not-an-object"})
        self.assertEqual(ctx.exception.code, 400)

    def test_i18n_endpoints(self):
        _status, en = self.get("/api/i18n/en")
        _status, zh = self.get("/api/i18n/zh-CN")
        self.assertEqual(en["language"], "en")
        self.assertEqual(zh["language"], "zh-CN")
        self.assertEqual(en["catalog"]["gui.render"], "Generate")
        self.assertEqual(zh["catalog"]["gui.render"], "生成")
        # 两个目录 key 集合一致（ 的 HTTP 侧体现）
        self.assertEqual(set(en["catalog"]), set(zh["catalog"]))

    def test_figures_localised(self):
        _status, en = self.get("/api/figures?lang=en")
        _status, zh = self.get("/api/figures?lang=zh-CN")
        self.assertEqual(en["language"], "en")
        self.assertEqual(en["figures"][0]["title"], "Single alcohol (condensed)")
        self.assertTrue(has_cjk(zh["figures"][0]["title"]))

    def test_english_mode_has_no_residual_chinese(self):
        """英文模式下，catalog 与参数表里不得出现汉字。"""
        _status, payload = self.get("/api/i18n/en")
        offenders = [(k, v) for k, v in payload["catalog"].items() if has_cjk(v)]
        self.assertEqual(offenders, [], "英文目录里还有中文：%s" % offenders[:5])

        _status, figs = self.get("/api/figures?lang=en")
        for figure in figs["figures"]:
            self.assertFalse(has_cjk(figure["title"]), figure["title"])
            self.assertFalse(has_cjk(figure["description"]), figure["description"])
            for param in figure["params"]:
                for field in ("label", "help"):
                    self.assertFalse(has_cjk(param[field]),
                                     "%s.%s = %r" % (figure["kind"], param["name"], param[field]))
                for label in param.get("choice_labels") or []:
                    self.assertFalse(has_cjk(label), label)


if __name__ == "__main__":
    unittest.main()


class GuiExportApiTests(unittest.TestCase):
    """导出接口：写盘 / 已存在先确认且不改 mtime / 覆盖 / 不可写（、12、13）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.out = cls.root / "exports"
        cls._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(cls.root / "cfg")
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.out)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = cls._old_cfg
        cls._tmp.cleanup()

    def post(self, payload):
        req = urllib.request.Request(self.base + "/api/export",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def base_payload(self, **kwargs):
        payload = {"kind": "alcohol", "params": {"smiles": "CCCCO"},
                   "dir": str(self.out), "filename": "gui_export.png",
                   "format": "png", "dpi": 300}
        payload.update(kwargs)
        return payload

    def test_export_writes_file(self):
        status, data = self.post(self.base_payload(filename="first.png"))
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        target = Path(data["path"])
        self.assertTrue(target.exists())
        self.assertEqual(target.parent, self.out)

    def test_existing_file_requires_confirmation_and_does_not_touch_mtime(self):
        self.post(self.base_payload(filename="dup.png"))
        target = self.out / "dup.png"
        before = target.stat().st_mtime_ns

        status, data = self.post(self.base_payload(filename="dup.png"))
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["needs_confirmation"])
        self.assertIn(str(target), data["existing"])
        self.assertEqual(target.stat().st_mtime_ns, before, "取消时不该动文件")

        # 确认覆盖 → 真的写
        status, data = self.post(self.base_payload(filename="dup.png", overwrite=True))
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(Path(data["path"]).name, "dup.png")

    def test_export_svg_and_pdf(self):
        _status, svg = self.post(self.base_payload(filename="gui.svg", format="svg"))
        self.assertTrue(svg["ok"])
        text = Path(svg["outputs"]["svg"]).read_text(encoding="utf-8")
        self.assertIn("<text", text)
        _status, pdf = self.post(self.base_payload(filename="gui.pdf", format="pdf"))
        self.assertTrue(pdf["ok"])
        self.assertEqual(b"%PDF-", Path(pdf["outputs"]["pdf"]).read_bytes()[:5])

    def test_unwritable_directory_reports_readable_error(self):
        status, data = self.post(self.base_payload(dir="/System/definitely-not-writable",
                                                   filename="nope.png"))
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertIn("writable", data["error"].lower() + data["error"])

    def test_bad_format_is_rejected(self):
        status, data = self.post(self.base_payload(format="tiff"))
        self.assertEqual(status, 400)

    def test_export_dialog_present_in_frontend(self):
        with urllib.request.urlopen(self.base + "/", timeout=20) as res:
            html = res.read().decode("utf-8")
        self.assertIn('id="export-panel"', html)
        self.assertIn("data-i18n=\"export.button\"", html)


class GuiPreviewAndDraftTests(unittest.TestCase):
    """阶段②：预览不落用户输出目录（/2）、草稿可存可读。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.out = cls.root / "out"
        cls.out.mkdir()
        cls._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(cls.root / "cfg")
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.out)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = cls._old_cfg
        cls._tmp.cleanup()

    def post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.loads(res.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as res:
            return json.loads(res.read().decode("utf-8"))

    def test_preview_writes_nothing_to_output_dir(self):
        before = {p.name for p in self.out.iterdir()}
        data = self.post("/api/preview", {"kind": "series", "params": {}})
        self.assertTrue(data["ok"], data)
        after = {p.name for p in self.out.iterdir()}
        self.assertEqual(before, after, "预览不该在用户输出目录留文件")
        # 预览分辨率必须够看（曾把 dpi 缺省当成 150 → 预览被缩到 197 px 就糊了）
        self.assertGreaterEqual(max(data["size"]), 300, "预览太小：%s" % (data["size"],))
        self.assertLessEqual(max(data["size"]), 1200, "预览过大：%s" % (data["size"],))
        with urllib.request.urlopen(self.base + data["url"], timeout=20) as res:
            self.assertEqual(res.read(8), b"\x89PNG\r\n\x1a\n")

    def test_preview_reports_bad_kind(self):
        req = urllib.request.Request(self.base + "/api/preview",
                                     data=json.dumps({"kind": "nope"}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(ctx.exception.code, 400)

    def test_window_state_persists(self):
        """外壳增强：记住布局（窗口尺寸 + 上次图类型）要走设置接口。"""
        payload = self.post("/api/settings",
                            {"changes": {"windowState": {"width": 1440, "height": 900,
                                                         "kind": "series"}}})
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["settings"]["windowState"]["kind"], "series")
        again = self.get("/api/settings")["settings"]
        self.assertEqual(again["windowState"]["width"], 1440)

    def test_drafts_roundtrip(self):
        payload = {"document": {"mode": "figure",
                                "figure": {"kind": "alcohol", "params": {"smiles": "CCCCO"}}}}
        saved = self.post("/api/documents", payload)
        self.assertTrue(saved["ok"], saved)
        self.assertTrue(Path(saved["path"]).exists())
        listing = self.get("/api/documents")
        self.assertTrue(any(item["id"] == saved["id"] for item in listing["drafts"]))
        loaded = self.get("/api/documents/" + saved["id"])
        self.assertEqual(loaded["document"]["figure"]["kind"], "alcohol")

    def test_draft_not_found_returns_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/documents/nope.cfgd.json")
        self.assertEqual(ctx.exception.code, 404)


class GuiBatchApiTests(unittest.TestCase):
    """批量导出的 GUI 接口（干跑 / 真跑 / 覆盖确认）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.out = cls.root / "batch-out"
        cls.out.mkdir()
        cls._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(cls.root / "cfg")
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.out)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = cls._old_cfg
        cls._tmp.cleanup()

    CSV = "name,smiles,delta_h\n甲醇,CO,-22.7\n乙醇,CCO,-36.1\n坏行,not-real,-1\n"

    def post(self, payload):
        req = urllib.request.Request(self.base + "/api/batch",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_dry_run_lists_rows_without_writing(self):
        before = {p.name for p in self.out.iterdir()}
        status, data = self.post({"csv": self.CSV, "dir": str(self.out), "dryRun": True})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["dryRun"])
        self.assertEqual(data["count"], 3)
        self.assertEqual([Path(t).name for t in data["targets"]],
                         ["01_甲醇.png", "02_乙醇.png", "03_坏行.png"])
        self.assertEqual({p.name for p in self.out.iterdir()}, before, "干跑不该写文件")

    def test_run_reports_success_and_failure(self):
        status, data = self.post({"csv": self.CSV, "dir": str(self.out), "format": "png",
                                  "dpi": 150, "filenameTemplate": "run-{index}"})
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["succeeded"], 2)
        self.assertEqual(data["failed"], 1)
        for name in ("run-1.png", "run-2.png"):
            self.assertTrue((self.out / name).exists(), name)

    def test_existing_files_require_confirmation(self):
        payload = {"csv": self.CSV, "dir": str(self.out), "dpi": 150,
                   "filenameTemplate": "dup-{index}"}
        self.post(payload)
        status, data = self.post(payload)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["needs_confirmation"])
        self.assertEqual(len(data["existing"]), 2)
        status, data = self.post(dict(payload, overwrite=True))
        self.assertTrue(data["ok"], data)

    def test_missing_csv_is_rejected(self):
        status, data = self.post({"dir": str(self.out)})
        self.assertEqual(status, 400)

    def test_batch_panel_present_in_frontend(self):
        with urllib.request.urlopen(self.base + "/", timeout=20) as res:
            html = res.read().decode("utf-8")
        self.assertIn('id="batch-panel"', html)
        self.assertIn('data-i18n="batch.run"', html)


class GuiPluginTests(unittest.TestCase):
    """阶段④：插件图类型要能出现在 GUI 的参数表里，并有 /api/plugins。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.plugins_dir = cls.root / "plugins"
        cls.plugins_dir.mkdir()
        (cls.plugins_dir / "gui_preset.figure.json").write_text(
            '{"kind": "gui_preset", "base": "chcount", "params": {"n_carbons": 4}}',
            encoding="utf-8")
        cls._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(cls.root / "cfg")
        cls._old_plugins = os.environ.get("CHEMFIG_PLUGINS")
        os.environ["CHEMFIG_PLUGINS"] = str(cls.plugins_dir)
        from chemfigstudio import api, plugins
        cls.api, cls.plugins = api, plugins
        cls._figures = dict(api.FIGURES)
        plugins.load_all(force=True)
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.root / "out")
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.api.FIGURES.clear()
        cls.api.FIGURES.update(cls._figures)
        cls.plugins._STATE["loaded"] = False
        if cls._old_plugins is None:
            os.environ.pop("CHEMFIG_PLUGINS", None)
        else:
            os.environ["CHEMFIG_PLUGINS"] = cls._old_plugins
        if cls._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = cls._old_cfg
        cls._tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as res:
            return json.loads(res.read().decode("utf-8"))

    def test_plugin_kind_appears_in_figures(self):
        data = self.get("/api/figures")
        kinds = [f["kind"] for f in data["figures"]]
        self.assertEqual(kinds[:4], ["alcohol", "series", "reaction", "chcount"])
        self.assertIn("gui_preset", kinds)

    def test_plugins_endpoint(self):
        data = self.get("/api/plugins")
        self.assertTrue(data["ok"])
        self.assertIn("gui_preset", data["plugins"]["kinds"])


class GuiSessionDocumentTests(unittest.TestCase):
    """GET /api/session 必须给出当前文档（客户端靠它读参数；骨架编辑依赖这一点）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(cls.root / "cfg")
        os.environ["CHEMFIG_PLUGINS"] = "off"
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.root / "out")
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = cls._old_cfg
        os.environ.pop("CHEMFIG_PLUGINS", None)
        cls._tmp.cleanup()

    def request(self, path, payload=None):
        if payload is None:
            with urllib.request.urlopen(self.base + path, timeout=30) as res:
                return json.loads(res.read().decode("utf-8"))
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))

    def test_get_session_returns_document(self):
        data = self.request("/api/session")
        self.assertTrue(data["ok"])
        self.assertIsNotNone(data["document"], "GET /api/session 必须带当前文档")
        self.assertIn("figure", data["document"])
        self.assertEqual(data["activeId"], data["session"]["activeId"])

    def test_rename_tab_roundtrip(self):
        created = self.request("/api/session", {"action": "newTab",
                                                "title": "before"})
        tab_id = created["activeId"]
        renamed = self.request("/api/session", {"action": "renameTab",
                                                "tabId": tab_id, "title": "after"})
        labels = {t["id"]: t["label"] for t in renamed["session"]["tabs"]}
        self.assertEqual(labels[tab_id], "after")

    def test_apply_then_read_back_skeleton(self):
        skeleton = ('{"title":"","atoms":[{"id":"c0","element":"C","x":100,"y":100,"label":"CH_{3}"}],'
                    '"bonds":[]}')
        self.request("/api/session", {"action": "apply", "kind": "skeleton",
                                      "params": {"skeleton": skeleton}})
        data = self.request("/api/session")
        figure = data["document"]["figure"]
        self.assertEqual(figure["kind"], "skeleton")
        self.assertEqual(json.loads(figure["params"]["skeleton"])["atoms"][0]["id"], "c0")

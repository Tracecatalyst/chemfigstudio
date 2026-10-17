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
from unittest import mock

from chemfigstudio import render3d as render3d_mod
from chemfigstudio import settings as settings_mod
from chemfigstudio.gui import server as gui_server


class GuiHttpTests(unittest.TestCase):
    """GUI 端到端（含写盘收口：只有 outdir 与白名单目录能写）。"""

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

    def allow_dir(self, directory):
        """把目录加入写盘白名单（走真实 /api/settings；返回失败时抛错便于定位）。"""
        status, payload = self.post("/api/settings",
                                    {"changes": {"allowedWriteDirs": [str(directory)]}})
        self.assertEqual(status, 200, payload)
        return directory

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
        # 第三方 3D 库放在 static/vendor/（子目录）—— 必须能取到，否则查看器白屏
        status, body = self.get("/static/vendor/3Dmol-min.js")
        self.assertEqual(status, 200)
        self.assertIn(b"3Dmol", body[:4000])

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
        # 导出文件名不能出现 x.png.png：用户自己写了扩展名时要先剥掉
        self.assertIn('.replace(/\\.[a-z0-9]+$/i, "") + "." + fmt', js,
                      "导出文件名没有剥掉用户输入的扩展名（会得到 x.png.png）")
        # 写盘白名单必须在设置面板里可见可编辑，否则功能等于被关掉
        self.assertIn("set-allowed-dirs", html)
        self.assertIn("allowedWriteDirs", js)
        _status, css = self.get("/static/style.css")
        css = css.decode("utf-8")
        # hidden 属性必须能压过 .sheet 的 display:flex（否则弹层一直罩住界面 —— 已踩过）
        self.assertIn(".sheet[hidden]", css)

    # ----------------------------------------------------------- 3D（ADR-012）

    def test_viewer3d_page_is_served(self):
        for path in ("/viewer3d", "/viewer3d.html"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            text = body.decode("utf-8")
            self.assertIn("3Dmol-min.js", text)               # 必须引用自托管的库
            self.assertNotIn("cdn", text.lower())             # 不许有 CDN（离线要求）
            self.assertIn("viewer3d.js", text)

    def test_preview_surfaces_valence_problems(self):
        """超价分子必须通过 HTTP 把 warnings 交给前端（否则 GUI 上等于没检查）。

        链路：figures.skeleton_figure → SaveReport.warnings → FigureResult.as_dict()
              → /api/preview 的 JSON → 前端 showWarnings()（显示成 ⚠）
        任意一环断了，用户都看不到"这个分子画错了"。
        """
        from chemfigstudio.skeleton import Skeleton

        def preview(params):
            status, payload = self.post("/api/preview",
                                        {"kind": "skeleton", "params": params})
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload.get("ok"), payload)
            return payload.get("warnings") or []

        # 干净分子：不能凭空冒出告警
        clean = Skeleton.chain(4)
        self.assertEqual(preview({"skeleton": clean.to_json()}), [])

        # 一个碳连 5 根键：必须报出来
        bad = Skeleton()
        carbon = bad.add_atom("C", 100.0, 100.0)
        for index in range(5):
            hydrogen = bad.add_atom("H", 100.0 + 80.0 * (index + 1), 100.0)
            bad.add_bond(carbon.id, hydrogen.id)
        warnings = preview({"skeleton": bad.to_json()})
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("价键", warnings[0])
        self.assertIn("5", warnings[0])

    def test_compare_page_is_served(self):
        """2D vs 3D 对照页：也是独立页面，同样自托管 3Dmol、无 CDN。"""
        for path in ("/compare", "/compare.html"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            text = body.decode("utf-8")
            self.assertIn("3Dmol-min.js", text)
            self.assertNotIn("cdn", text.lower())
            self.assertIn("compare.js", text)
            self.assertIn("compare.css", text)

    def test_compare_page_reuses_existing_endpoints(self):
        """对照页必须**复用**已有端点，不能偷偷加新 API。

        这是架构约束（见 ARCHITECTURE §3「界面只有一份」+ 不重复实现）：
        2D 走 /api/preview（不落盘），3D 走 /api/render3d（只回几何数据）。
        """
        _status, body = self.get("/static/compare.js")
        js = body.decode("utf-8")
        self.assertIn("/api/preview", js)
        self.assertIn("/api/render3d", js)
        # 不许自己解析 SMILES / 自己算坐标。注意 molblock **会**出现（那是 API 返回的
        # 字段名，前端只是把它交给 3Dmol），所以只拦"自己算"的证据。
        for forbidden in ("Compute2DCoords", "MolFromSmiles", "rdkit", "EmbedMolecule"):
            self.assertNotIn(forbidden, js,
                             "compare.js 里出现了 %r —— 几何应该在服务端算" % forbidden)

    def test_compare_page_serialises_the_2d_requests(self):
        """2D 请求必须**串行**。

        `api.preview()` 写的是固定文件名 `preview.png`。对照页要并排显示多个分子，
        如果 6 个 2D 请求并发，它们会互相覆盖 —— 最后所有行都显示同一个分子。
        所以前端必须有一条串行队列（`twoDChain`）。
        """
        _status, body = self.get("/static/compare.js")
        js = body.decode("utf-8")
        # 只看"twoDChain 这个名字存在"是**假守卫** —— 请求可以不挂在链上。
        # 必须断言真的用 `.then(` 把下一次请求接在上一次的 Promise 后面。
        self.assertIn("twoDChain.then(", js,
                      "2D 请求没有挂在串行链上 → 6 个分子会互相覆盖 preview.png")
        self.assertIn("twoDChain = run", js,
                      "串行链没有延续（下一次请求不会等上一次）")
        self.assertIn("queue2D", js)

    def test_compare_page_discards_stale_results(self):
        """重渲染后，上一轮的异步结果不能再画上去（用户连点/切分子时）。"""
        _status, body = self.get("/static/compare.js")
        js = body.decode("utf-8")
        self.assertIn("CMP.token", js)
        self.assertIn("token !== CMP.token", js)

    def test_compare_page_builds_3d_lazily(self):
        """3D 必须惰性初始化，否则多个分子一起建 WebGL context 会撞浏览器上限。"""
        _status, body = self.get("/static/compare.js")
        js = body.decode("utf-8")
        self.assertIn("IntersectionObserver", js)
        self.assertIn("maybeBuild3D", js)

    def test_main_ui_links_to_compare(self):
        """主界面必须有入口按钮，否则功能等于不存在。"""
        _status, body = self.get("/")
        html = body.decode("utf-8")
        self.assertIn("open-compare", html)
        _status, js = self.get("/static/app.js")
        self.assertIn("openCompare", js.decode("utf-8"))

    def test_viewer3d_assets_are_local(self):
        for asset in ("/static/viewer3d.js", "/static/viewer3d.css"):
            status, body = self.get(asset)
            self.assertEqual(status, 200, asset)
            self.assertTrue(body)
        _status, js = self.get("/static/viewer3d.js")
        js = js.decode("utf-8")
        for hook in ("/api/render3d", "pngURI", "$3Dmol", "data-i18n"):
            self.assertIn(hook, js, "viewer3d.js 缺少 %s" % hook)

    def test_render3d_returns_geometry_not_image(self):
        """路由契约：回的是 molblock 几何数据，不是图片文件。"""
        fake = render3d_mod.Conformer3D(
            smiles="CCO", molblock="\n     RDKit          3D\n\n  3  2  0  0  0  0  0  0  0  0999 V2000\nM  END\n",
            atoms=9, heavy_atoms=3, formula="C2H6O", mw=46.07, energy=-1.337,
            method="ETKDGv3 + MMFF94")
        with mock.patch.object(render3d_mod, "embed3d", return_value=fake):
            status, payload = self.post("/api/render3d", {"smiles": "CCO"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["format"], "sdf")
        self.assertIn("M  END", payload["molblock"])
        self.assertEqual(payload["formula"], "C2H6O")
        self.assertEqual(payload["energy"], -1.337)
        # 服务端不写任何文件：预览与输出目录都不该多出东西
        self.assertEqual([p.name for p in self.outdir.iterdir()], [])

    def test_render3d_missing_rdkit_is_503(self):
        """缺 RDKit 是**环境能力**缺失（503），不是用户参数写错（400）。"""
        from chemfigstudio.errors import ConfigError
        with mock.patch.object(render3d_mod, "embed3d",
                               side_effect=ConfigError(render3d_mod.RDKIT_3D_HINT)):
            status, payload = self.post("/api/render3d", {"smiles": "CCO"})
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload.get("needRdkit"))
        self.assertIn("rdkit", payload["error"].lower())

    def test_render3d_bad_smiles_is_400(self):
        from chemfigstudio.errors import RenderError
        with mock.patch.object(render3d_mod, "embed3d", side_effect=RenderError("无法解析")):
            status, payload = self.post("/api/render3d", {"smiles": "!!!"})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_render3d_empty_smiles_is_400(self):
        status, payload = self.post("/api/render3d", {"smiles": "   "})
        self.assertEqual(status, 400)
        self.assertIn("smiles", payload["error"])

    def test_render3d_bad_seed_is_400(self):
        status, payload = self.post("/api/render3d", {"smiles": "CCO", "seed": "abc"})
        self.assertEqual(status, 400)
        self.assertIn("seed", payload["error"])

    def test_render3d_options_are_passed_through(self):
        seen = {}

        def fake_embed(smiles, **kwargs):
            seen["smiles"] = smiles
            seen.update(kwargs)
            return render3d_mod.Conformer3D(smiles=smiles, molblock="M  END\n",
                                            atoms=9, heavy_atoms=3)

        with mock.patch.object(render3d_mod, "embed3d", side_effect=fake_embed):
            status, _payload = self.post("/api/render3d",
                                         {"smiles": "CCO", "seed": 7, "conformers": 2,
                                          "optimise": False})
        self.assertEqual(status, 200)
        self.assertEqual(seen, {"smiles": "CCO", "seed": 7, "conformers": 2,
                                "optimise": False})

    def test_static_subdir_traversal_is_still_blocked(self):
        """允许 vendor/ 子目录 ≠ 放开穿越：逃出 static/ 一律 403。"""
        for path in ("/static/../../etc/passwd", "/static/vendor/../../server.py",
                     "/static/.hidden", "/static/vendor/.secret"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self.get(path)
                self.assertIn(ctx.exception.code, (403, 404))

    def test_static_can_serve_nested_asset(self):
        """静态目录内的**子目录**资源必须可取（vendor/3Dmol-min.js 就靠这条）。

        以前 _safe_child 一律拒绝含 "/" 的名字 → vendor 库 403 → 查看器直接白屏。
        """
        status, body = self.get("/static/vendor/3Dmol-LICENSE.txt")
        self.assertEqual(status, 200)
        self.assertIn(b"BSD", body)

    # ------------------------------------- 批量覆盖确认必须与真实写入一致（数据丢失）

    def test_batch_dry_run_targets_match_what_is_written(self):
        """dryRun 报出的文件名，必须与真正会写出的文件**逐个相同**。

        曾经 target_paths() 用 fallback "figure"、run_batch() 用 kind（默认 alcohol），
        于是确认框检查 01_figure.png、实际覆盖 01_alcohol.png —— 静默数据丢失。
        """
        import shutil
        work = Path(tempfile.mkdtemp())
        try:
            self.allow_dir(work)
            csv_text = "name,smiles\n,CO\n"
            # 先用不带 kind 列的最小 CSV 干跑，拿到"将要写"的文件名
            status, dry = self.post("/api/batch", {"csv": csv_text, "dir": str(work),
                                                   "dryRun": True, "format": "png"})
            self.assertEqual(status, 200)
            self.assertTrue(dry["dryRun"])
            targets = [Path(t) for t in dry["targets"]]
            self.assertEqual(len(targets), 1)

            # 关键断言：干跑说的文件 = 真跑写的文件
            status, ran = self.post("/api/batch", {"csv": csv_text, "dir": str(work),
                                                   "format": "png", "dpi": 72,
                                                   "overwrite": True})
            self.assertEqual(status, 200, ran)
            self.assertTrue(ran["ok"], ran)
            produced = sorted(p.name for p in work.iterdir())
            self.assertEqual(produced, [targets[0].name],
                             "干跑与真跑的文件名不一致 → 覆盖确认检查了另一批文件")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_batch_refuses_to_overwrite_without_confirmation(self):
        """已存在同名文件时，未带 overwrite 必须回 needs_confirmation 且不落盘。"""
        import shutil
        work = Path(tempfile.mkdtemp())
        try:
            self.allow_dir(work)
            csv_text = "name,smiles\n,CO\n"
            status, dry = self.post("/api/batch", {"csv": csv_text, "dir": str(work),
                                                   "dryRun": True, "format": "png"})
            target = Path(dry["targets"][0])
            target.write_bytes(b"PRECIOUS")           # 假装用户已有文件
            status, payload = self.post("/api/batch", {"csv": csv_text, "dir": str(work),
                                                       "format": "png", "dpi": 72})
            self.assertEqual(status, 200)
            self.assertTrue(payload.get("needs_confirmation"), payload)
            self.assertEqual(target.read_bytes(), b"PRECIOUS", "未经确认就动了已有文件")
        finally:
            shutil.rmtree(work, ignore_errors=True)

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


CJK = range(0x4E00, 0x9FFF)          # 汉字区间；验收 16 用它扫"残留中文"


def has_cjk(text: str) -> bool:
    return any(ord(ch) in CJK for ch in text or "")


class GuiSettingsAndI18nTests(unittest.TestCase):
    """设置接口 / i18n 接口 / 英文模式无残留中文（验收 15、16）。"""

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
        # 两个目录 key 集合一致（验收 17 的 HTTP 侧体现）
        self.assertEqual(set(en["catalog"]), set(zh["catalog"]))

    def test_figures_localised(self):
        _status, en = self.get("/api/figures?lang=en")
        _status, zh = self.get("/api/figures?lang=zh-CN")
        self.assertEqual(en["language"], "en")
        self.assertEqual(en["figures"][0]["title"], "Single alcohol (condensed)")
        self.assertTrue(has_cjk(zh["figures"][0]["title"]))

    def test_english_mode_has_no_residual_chinese(self):
        """验收 16：英文模式下，catalog 与参数表里不得出现汉字。"""
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


class GuiRequestBoundaryTests(unittest.TestCase):
    """HTTP 请求边界（审计 security-01/02/03）：写盘范围、跨站、Host、Content-Type。

    这些不是"学术性"检查 —— 服务虽然只监听回环，但**用户浏览器里的任何网页**都能
    向 127.0.0.1 发跨站简单请求；修之前已复现"请求指定任意 dir 就把文件写到那里"。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.out = cls.root / "out"
        cls.elsewhere = cls.root / "elsewhere"
        cls.elsewhere.mkdir(parents=True, exist_ok=True)
        cls._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(cls.root / "cfg")
        cls.cfg = settings_mod.Settings.load()
        cls.httpd, cls.port = gui_server.make_server(port=0, outdir=cls.out, cfg=cls.cfg)
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

    def raw_post(self, path, body, headers=None):
        """发一个**原始**请求（可自定义 Origin/Host/Content-Type，模拟跨站页面）。"""
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def export_payload(self, directory=None, filename="x.png"):
        payload = {"kind": "alcohol", "params": {"smiles": "CCO"},
                   "filename": filename, "format": "png", "dpi": 72}
        if directory is not None:
            payload["dir"] = str(directory)
        return payload

    # ------------------------------------------------ security-01：写盘范围

    def test_export_outside_outdir_is_rejected(self):
        status, payload = self.raw_post("/api/export", self.export_payload(self.elsewhere),
                                        {"Content-Type": "application/json"})
        self.assertEqual(status, 403, payload)
        self.assertFalse(payload["ok"])
        self.assertIn("范围", payload["error"])
        self.assertEqual(list(self.elsewhere.iterdir()), [], "越界请求竟然写了文件")

    def test_batch_outside_outdir_is_rejected(self):
        status, payload = self.raw_post("/api/batch",
                                        {"csv": "name,smiles\n,CO\n",
                                         "dir": str(self.elsewhere), "dryRun": True},
                                        {"Content-Type": "application/json"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(list(self.elsewhere.iterdir()), [])

    def test_explicit_allowlist_permits_writing_there(self):
        """用户显式加白名单后，就该能写 —— 否则功能被修死了。"""
        status, payload = self.raw_post("/api/settings",
                                        {"changes": {"allowedWriteDirs": [str(self.elsewhere)]}},
                                        {"Content-Type": "application/json"})
        self.assertEqual(status, 200, payload)
        try:
            status, payload = self.raw_post("/api/export",
                                            self.export_payload(self.elsewhere, "allowed.png"),
                                            {"Content-Type": "application/json"})
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload["ok"])
            self.assertTrue((self.elsewhere / "allowed.png").exists())
        finally:
            self.raw_post("/api/settings", {"changes": {"allowedWriteDirs": []}},
                          {"Content-Type": "application/json"})
            for leftover in self.elsewhere.iterdir():
                leftover.unlink()

    def test_default_write_root_is_outdir(self):
        status, payload = self.raw_post("/api/export", self.export_payload(None, "plain.png"),
                                        {"Content-Type": "application/json"})
        self.assertEqual(status, 200, payload)
        self.assertTrue((self.out / "plain.png").exists())

    # ------------------------------------------------ security-03：跨站

    def test_cross_site_origin_is_rejected(self):
        status, payload = self.raw_post("/api/export", self.export_payload(self.out, "evil.png"),
                                        {"Content-Type": "application/json",
                                         "Origin": "https://evil.example",
                                         "Host": "127.0.0.1:%d" % self.port})
        self.assertEqual(status, 403, payload)
        self.assertIn("跨站", payload["error"])

    def test_cross_site_settings_change_is_rejected(self):
        """恶意页面不能借用户之手改设置（改了 outputDir 就等于改了写盘根）。"""
        status, payload = self.raw_post("/api/settings",
                                        {"changes": {"outputDir": str(self.elsewhere)}},
                                        {"Content-Type": "application/json",
                                         "Origin": "http://evil.example"})
        self.assertEqual(status, 403, payload)
        self.assertNotEqual(self.cfg.get("outputDir"), str(self.elsewhere))

    def test_non_json_content_type_is_rejected(self):
        """跨站"简单请求"只能发 text/plain / 表单，必须在这里被挡住。"""
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(ctype=ctype):
                status, payload = self.raw_post("/api/settings",
                                                {"changes": {"outputDir": "/tmp/x"}},
                                                {"Content-Type": ctype})
                self.assertEqual(status, 403, payload)
                self.assertIn("json", payload["error"].lower())

    def test_dns_rebinding_host_is_rejected(self):
        status, payload = self.raw_post("/api/export", self.export_payload(self.out, "r.png"),
                                        {"Content-Type": "application/json",
                                         "Host": "evil.example"})
        self.assertEqual(status, 403, payload)
        self.assertIn("Host", payload["error"])

    def test_same_origin_loopback_is_allowed(self):
        status, payload = self.raw_post("/api/export", self.export_payload(self.out, "same.png"),
                                        {"Content-Type": "application/json",
                                         "Origin": "http://127.0.0.1:%d" % self.port,
                                         "Host": "127.0.0.1:%d" % self.port})
        self.assertEqual(status, 200, payload)
        self.assertTrue((self.out / "same.png").exists())

    def test_localhost_origin_is_allowed(self):
        """用 localhost 打开界面时也不能被自己挡住。"""
        status, payload = self.raw_post("/api/export", self.export_payload(self.out, "lh.png"),
                                        {"Content-Type": "application/json",
                                         "Origin": "http://localhost:%d" % self.port,
                                         "Host": "localhost:%d" % self.port})
        self.assertEqual(status, 200, payload)

    def test_get_requests_are_not_blocked(self):
        """只拦写操作：GET（静态资源/健康检查）不受 Origin 限制。"""
        status, body = 200, b""
        with urllib.request.urlopen(self.base + "/api/health", timeout=20) as res:
            status, body = res.status, res.read()
        self.assertEqual(status, 200)
        self.assertIn(b"ok", body)


class GuiExportApiTests(unittest.TestCase):
    """导出接口：写盘 / 已存在先确认且不改 mtime / 覆盖 / 不可写（验收 7、12、13）。"""

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

    def post(self, payload, path="/api/export"):
        req = urllib.request.Request(self.base + path,
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
        # 先加白名单：这样测的才是"目录不可写"（400），而不是"越界"（403）
        status, _payload = self.post(
            {"changes": {"allowedWriteDirs": ["/System/definitely-not-writable"]}},
            path="/api/settings")
        self.assertEqual(status, 200)
        status, data = self.post(self.base_payload(dir="/System/definitely-not-writable",
                                                   filename="nope.png"))
        self.assertEqual(status, 400, data)
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
    """阶段②：预览不落用户输出目录（验收 1/2）、草稿可存可读（验收 3）。"""

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
    """验收 14：批量导出的 GUI 接口（干跑 / 真跑 / 覆盖确认）。"""

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

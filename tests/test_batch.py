"""P1 待办：RDKit 自动发现（CHEMFIG_PYLIBS / ./.pylibs）与批量出图。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from chemfigstudio import batch
from chemfigstudio.errors import RenderError
from chemfigstudio import smiles as smiles_mod


class BatchTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(self.tmp / "cfg")

    def tearDown(self):
        if self._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = self._old_cfg
        self._tmp.cleanup()

    def write_csv(self, text: str, name: str = "molecules.csv") -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


class ReadCsvTests(BatchTestCase):
    def test_basic_rows(self):
        path = self.write_csv("name,smiles,delta_h\n甲醇,CO,-22.7\n乙醇,CCO,-36.1\n")
        items = batch.read_csv(path)
        self.assertEqual([i.name for i in items], ["甲醇", "乙醇"])
        self.assertEqual(items[1].smiles, "CCO")
        self.assertEqual(items[0].delta_h, "-22.7")
        self.assertEqual([i.line for i in items], [2, 3])

    def test_comments_blank_lines_and_bom(self):
        path = self.tmp / "bom.csv"
        path.write_text("\ufeff# 注释\nname,smiles\n\n乙醇,CCO\n", encoding="utf-8")
        items = batch.read_csv(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].smiles, "CCO")

    def test_chinese_headers_and_n_carbons(self):
        path = self.write_csv("名称,smiles,焓变,碳数\n丁醇,CCCCO,-36.1,4\n")
        items = batch.read_csv(path)
        self.assertEqual(items[0].name, "丁醇")
        self.assertEqual(items[0].delta_h, "-36.1")
        self.assertEqual(items[0].n_carbons, 4)

    def test_missing_smiles_column_is_rejected(self):
        path = self.write_csv("name,delta_h\n甲醇,-22.7\n")
        with self.assertRaises(RenderError) as ctx:
            batch.read_csv(path)
        self.assertIn("smiles", str(ctx.exception))

    def test_missing_file_and_empty_file(self):
        with self.assertRaises(RenderError):
            batch.read_csv(self.tmp / "nope.csv")
        empty = self.write_csv("# 只有注释\n", "empty.csv")
        with self.assertRaises(RenderError):
            batch.read_csv(empty)


class RunBatchTests(BatchTestCase):
    def items(self):
        return batch.read_csv(self.write_csv(
            "name,smiles,delta_h\n甲醇,CO,-22.7\n乙醇,not-a-smiles,-36.1\n丙醇,CCCO,\n"))

    def test_good_rows_render_and_bad_row_is_reported(self):
        report = batch.run_batch(self.items(), self.tmp / "out", dpi=150)
        self.assertEqual(report.total, 3)
        self.assertEqual(len(report.succeeded), 2)
        self.assertEqual(len(report.failed), 1)
        self.assertIn("not-a-smiles", report.failed[0]["smiles"])
        for item in report.succeeded:
            for path in item["outputs"].values():
                self.assertTrue(Path(path).exists(), path)

    def test_progress_callback_and_templates(self):
        seen = []
        report = batch.run_batch(self.items(), self.tmp / "out2", dpi=150,
                                 filename_template="{index}-{name}",
                                 on_progress=lambda i, total, item: seen.append((i, total)))
        self.assertEqual(seen[0], (1, 3))
        self.assertEqual(len(seen), 3)
        names = sorted(Path(i["path"]).name for i in report.succeeded)
        # 模板里的 {index} 不补零（默认模板才用 {index:02d}）
        self.assertTrue(names[0].startswith("1-"), names)

    def test_warnings_are_aggregated(self):
        report = batch.run_batch(self.items(), self.tmp / "out3", dpi=150)
        self.assertIsInstance(report.warnings, list)

    def test_bad_format_is_rejected(self):
        with self.assertRaises(RenderError):
            batch.run_batch(self.items(), self.tmp / "out4", formats=("tiff",))

    def test_svg_and_pdf_formats(self):
        report = batch.run_batch(self.items()[:1], self.tmp / "out5",
                                 formats=("png", "svg", "pdf"), dpi=150)
        outputs = report.succeeded[0]["outputs"]
        self.assertEqual(sorted(outputs), ["pdf", "png", "svg"])

    def test_report_as_dict(self):
        payload = batch.run_batch(self.items()[:1], self.tmp / "out6", dpi=150).as_dict()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["succeeded"], 1)
        self.assertEqual(payload["failed"], 0)


class ReadCsvTextTests(unittest.TestCase):
    """GUI 走文本接口（免 multipart）：read_csv_text + target_paths。"""

    def test_text_parsing(self):
        items = batch.read_csv_text("name,smiles\n甲醇,CO\n乙醇,CCO\n")
        self.assertEqual([i.smiles for i in items], ["CO", "CCO"])

    def test_text_without_smiles_column_is_rejected(self):
        with self.assertRaises(RenderError) as ctx:
            batch.read_csv_text("name,delta_h\n甲醇,-22.7\n")
        self.assertIn("smiles", str(ctx.exception))

    def test_empty_text_is_rejected(self):
        with self.assertRaises(RenderError):
            batch.read_csv_text("# 只有注释\n")

    def test_target_paths_do_not_touch_disk(self):
        import tempfile
        from pathlib import Path
        items = batch.read_csv_text("name,smiles\n甲醇,CO\n乙醇,CCO\n")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            targets = batch.target_paths(items, directory, formats=("svg",))
            self.assertEqual([p.name for p in targets], ["01_甲醇.svg", "02_乙醇.svg"])
            self.assertEqual(list(directory.iterdir()), [], "干跑不该落盘")


class WatchTests(unittest.TestCase):
    """P3：--watch —— CSV 一变就重出图（循环带迭代上限，便于测试）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old_cfg = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(self.tmp / "cfg")

    def tearDown(self):
        if self._old_cfg is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = self._old_cfg
        self._tmp.cleanup()

    def test_renders_initially_then_only_on_change(self):
        csv_path = self.tmp / "m.csv"
        csv_path.write_text("name,smiles\n甲醇,CO\n", encoding="utf-8")
        outdir = self.tmp / "out"
        calls = {"n": 0}

        def fake_sleep(_seconds):
            """第一次"睡"时改文件，模拟用户编辑。"""
            calls["n"] += 1
            if calls["n"] == 1:
                csv_path.write_text("name,smiles\n甲醇,CO\n乙醇,CCO\n", encoding="utf-8")

        result = batch.watch(csv_path, outdir, dpi=150, interval=0.0, iterations=4,
                             sleep=fake_sleep)
        self.assertEqual(result["renders"], 2, "首轮 + 改动后一轮，共 2 次出图")
        self.assertEqual(result["runs"], 4)
        self.assertTrue((outdir / "01_甲醇.png").exists())
        self.assertTrue((outdir / "02_乙醇.png").exists())

    def test_invalid_csv_does_not_kill_the_watcher(self):
        csv_path = self.tmp / "bad.csv"
        csv_path.write_text("name,delta_h\n甲醇,-22.7\n", encoding="utf-8")   # 缺 smiles 列
        events = []
        result = batch.watch(csv_path, self.tmp / "out", interval=0.0, iterations=2,
                             sleep=lambda _s: None, on_event=events.append)
        self.assertEqual(result["renders"], 0)
        self.assertTrue(events[0]["error"])
        self.assertIn("smiles", events[0]["error"])

    def test_missing_file_is_reported_not_raised(self):
        result = batch.watch(self.tmp / "nope.csv", self.tmp / "out", interval=0.0,
                             iterations=2, sleep=lambda _s: None)
        self.assertTrue(all(event.get("error") for event in result["events"]))

    def test_on_event_receives_counts(self):
        csv_path = self.tmp / "ok.csv"
        csv_path.write_text("name,smiles\n甲醇,CO\n坏行,nope\n", encoding="utf-8")
        events = []
        batch.watch(csv_path, self.tmp / "out", dpi=150, interval=0.0, iterations=1,
                    sleep=lambda _s: None, on_event=events.append)
        self.assertEqual(events[0]["succeeded"], 1)
        self.assertEqual(events[0]["failed"], 1)


class FindingFixesTests(unittest.TestCase):
    """功能测试（10 道真实题目）暴露的 3 个发现 → 回归测试。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._old = os.environ.get("CHEMFIG_CONFIG_DIR")
        os.environ["CHEMFIG_CONFIG_DIR"] = str(self.tmp / "cfg")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CHEMFIG_CONFIG_DIR", None)
        else:
            os.environ["CHEMFIG_CONFIG_DIR"] = self._old
        self._tmp.cleanup()

    # F1：SMILES 里没有 O（不是醇）时，报错要直接点出"甲醇请用 CO"
    def test_f1_smiles_without_oxygen_hints_methanol(self):
        from chemfigstudio import api
        with self.assertRaises(RenderError) as ctx:
            api.render("alcohol", out=str(self.tmp / "x.png"), params={"smiles": "C"},
                       formats=("png",))
        message = str(ctx.exception)
        self.assertIn("CO", message)
        self.assertIn("甲醇", message)

    def test_f1_normal_smiles_has_no_extra_hint(self):
        from chemfigstudio import api
        result = api.render("alcohol", out=str(self.tmp / "ok.png"),
                            params={"smiles": "CO"}, formats=("png",), dpi=150)
        self.assertTrue(Path(result.outputs["png"]).exists(), "CO 应该能画甲醇")

    # F3：str 参数收到 bool 必须报错，而不是把 "True" 画进图里
    def test_f3_bool_for_text_param_is_rejected(self):
        from chemfigstudio import api
        with self.assertRaises(RenderError) as ctx:
            api.render("chcount", out=str(self.tmp / "y.png"),
                       params={"n_carbons": 2, "summary": True}, formats=("png",))
        self.assertIn("文本", str(ctx.exception))

    def test_f3_text_param_still_accepts_text(self):
        from chemfigstudio import api
        result = api.render("chcount", out=str(self.tmp / "z.png"),
                            params={"n_carbons": 2, "summary": "全部是 C-H"}, formats=("png",),
                            dpi=150)
        self.assertTrue(Path(result.outputs["png"]).exists())

    # F2：series 现在能画出"含甲醇"的 6 行
    def test_f2_series_can_include_methanol(self):
        from chemfigstudio import api
        five = api.render("series", out=str(self.tmp / "5.png"), params={},
                          formats=("png",), dpi=150)
        six = api.render("series", out=str(self.tmp / "6.png"),
                         params={"include_methanol": True}, formats=("png",), dpi=150)
        self.assertGreater(six.size[1], five.size[1], "6 行应比 5 行更高")
        self.assertEqual(six.warnings, [])

    def test_f2_custom_entries(self):
        from chemfigstudio import api
        rows = [["methanol", "CH_{3}OH", 1, "−22.7"], ["ethanol", "C_{2}H_{5}OH", 2, "−29.7"]]
        result = api.render("series", out=str(self.tmp / "c.png"),
                            params={"entries": json.dumps(rows)}, formats=("png",), dpi=150)
        self.assertTrue(Path(result.outputs["png"]).exists())

    def test_f2_bad_entries_report_clearly(self):
        from chemfigstudio import api
        with self.assertRaises(RenderError) as ctx:
            api.render("series", out=str(self.tmp / "b.png"), params={"entries": "{not json"},
                       formats=("png",))
        self.assertIn("entries", str(ctx.exception))

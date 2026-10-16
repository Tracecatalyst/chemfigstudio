"""i18n 测试：catalog 完整性、回退链、语言解析、占位符。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from chemfigstudio import i18n


class CatalogTests(unittest.TestCase):
    def test_both_catalogs_exist_and_are_non_trivial(self):
        for code in i18n.SUPPORTED:
            data = i18n.catalog(code)
            self.assertGreater(len(data), 50, "%s 目录太小" % code)

    def test_key_sets_are_identical(self):
        """中英 catalog 的 key 集合完全一致。"""
        self.assertEqual(i18n.missing_keys("zh-CN"), [])
        self.assertEqual(i18n.missing_keys("en"), [])
        self.assertEqual(i18n.extra_keys("zh-CN"), [])
        self.assertEqual(i18n.extra_keys("en"), [])

    def test_comment_keys_are_ignored(self):
        self.assertFalse([k for k in i18n.catalog("zh-CN") if k.startswith("_")])

    def test_every_figure_kind_has_title_and_desc(self):
        from chemfigstudio import api
        for kind in api.FIGURES:
            for suffix in ("title", "desc"):
                key = "figure.%s.%s" % (kind, suffix)
                for code in i18n.SUPPORTED:
                    self.assertIn(key, i18n.catalog(code), "%s 缺少 %s" % (code, key))

    def test_every_param_has_label(self):
        from chemfigstudio import api
        for spec in api.FIGURES.values():
            for param in spec.params:
                key = "param.%s.label" % param.name
                for code in i18n.SUPPORTED:
                    self.assertIn(key, i18n.catalog(code), "%s 缺少 %s" % (code, key))


class LanguageTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("auto")

    def test_resolve_language_aliases(self):
        cases = {
            "auto": None, "zh": "zh-CN", "zh-CN": "zh-CN", "zh_CN": "zh-CN",
            "zh-Hans-CN": "zh-CN", "en": "en", "en-US": "en", "EN": "en",
            "fr": "en", "": None,
        }
        for raw, expected in cases.items():
            got = i18n.resolve_language(raw)
            if expected is None:
                self.assertIn(got, i18n.SUPPORTED, raw)
            else:
                self.assertEqual(got, expected, raw)

    def test_system_language_is_supported(self):
        self.assertIn(i18n.system_language(), i18n.SUPPORTED)

    def test_available_languages_includes_auto(self):
        codes = [item["code"] for item in i18n.available_languages()]
        self.assertEqual(codes[0], "auto")
        self.assertIn("zh-CN", codes)
        self.assertIn("en", codes)

    def test_env_var_override(self):
        old = os.environ.get("CHEMFIG_LANG")
        try:
            os.environ["CHEMFIG_LANG"] = "en_US.UTF-8"
            self.assertEqual(i18n.system_language(), "en")
            os.environ["CHEMFIG_LANG"] = "zh_CN.UTF-8"
            self.assertEqual(i18n.system_language(), "zh-CN")
        finally:
            if old is None:
                os.environ.pop("CHEMFIG_LANG", None)
            else:
                os.environ["CHEMFIG_LANG"] = old


class TranslateTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("auto")

    def test_switch_language_changes_text(self):
        i18n.set_language("zh-CN")
        zh = i18n.t("app.tagline")
        i18n.set_language("en")
        en = i18n.t("app.tagline")
        self.assertNotEqual(zh, en)
        self.assertIn("化学图", zh)
        self.assertIn("Textbook", en)

    def test_unknown_key_returns_key_itself(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("no.such.key"), "no.such.key")

    def test_placeholders_are_formatted(self):
        i18n.set_language("en")
        text = i18n.t("cli.done", path="/tmp/a.png", size="(10, 10)")
        self.assertIn("/tmp/a.png", text)
        self.assertIn("(10, 10)", text)

    def test_bad_placeholder_does_not_crash(self):
        i18n.set_language("en")
        text = i18n.t("cli.done", wrong="x")               # 缺 path/size
        self.assertIn("{path}", text)                      # 原样返回

    def test_missing_catalog_falls_back_to_english(self):
        """语言目录缺失时回退到 en，而不是空白。"""
        i18n.set_language("en")
        before = i18n.t("app.name")
        self.assertEqual(before, "chemfigstudio")

    def test_chinese_catalog_has_no_latin_only_strings_for_ui(self):
        """抽样检查：核心 UI 文案在中文目录里确实是中文（ 的反向保障）。"""
        for key in ("gui.render", "settings.save", "export.button", "gui.preview"):
            text = i18n.catalog("zh-CN")[key]
            self.assertTrue(any("\u4e00" <= ch <= "\u9fff" for ch in text),
                            "%s 应该是中文：%r" % (key, text))


if __name__ == "__main__":
    unittest.main()


class PurityTests(unittest.TestCase):
    """隐患修复：按语言取词必须是纯函数，不能改全局状态（多线程安全的前提）。"""

    def tearDown(self):
        i18n.set_language("auto")

    def test_text_with_lang_does_not_change_global_language(self):
        i18n.set_language("zh-CN")
        before = i18n.current_language()
        for _ in range(50):
            self.assertEqual(i18n.text("gui.render", "en"), "Generate")
            self.assertEqual(i18n.text("gui.render", "zh-CN"), "生成")
        self.assertEqual(i18n.current_language(), before, "取词不该改全局语言")
        self.assertEqual(i18n.t("gui.render"), "生成")

    def test_api_resolve_text_is_pure(self):
        from chemfigstudio import api
        i18n.set_language("zh-CN")
        before = i18n.current_language()
        self.assertEqual(api.resolve_text("figure.alcohol.title", "en"),
                         "Single alcohol (condensed)")
        self.assertEqual(i18n.current_language(), before)
        self.assertEqual(api.list_figures("en")[0]["title"], "Single alcohol (condensed)")
        self.assertEqual(i18n.current_language(), before, "list_figures(lang) 不该改全局")

    def test_parallel_language_requests_never_mix(self):
        """并发压测：多线程同时要不同语言，结果不得串。"""
        import threading
        from chemfigstudio import api
        i18n.set_language("zh-CN")
        bad = []
        lock = threading.Lock()

        def worker(lang):
            for _ in range(200):
                title = api.list_figures(lang)[0]["title"]
                cjk = any("\u4e00" <= ch <= "\u9fff" for ch in title)
                if (lang == "en" and cjk) or (lang == "zh-CN" and not cjk):
                    with lock:
                        bad.append((lang, title))
                    return

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=worker, args=("en",)))
            threads.append(threading.Thread(target=worker, args=("zh-CN",)))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(bad, [], "并发取词串语言了：%s" % bad[:3])

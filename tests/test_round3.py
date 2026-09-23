"""第三轮的测试：个人规则、阈值调整、数据目录迁移。

    python -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RECKON_DATA", tempfile.mkdtemp(prefix="reckon_test_"))

from reckon import config as C  # noqa: E402
from reckon import decide  # noqa: E402
from reckon import dupes  # noqa: E402
from reckon import organize  # noqa: E402
from reckon import recycle  # noqa: E402
from reckon import rules  # noqa: E402
from reckon import server  # noqa: E402
from test_safety import make_app, write  # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sent = []
        self.patches = [mock.patch("send2trash.send2trash", side_effect=self.sent.append),
                        mock.patch.object(recycle, "check", return_value=None),
                        mock.patch.object(C, "RESULTS_FILE", os.path.join(self.tmp, "last_scan.json")),
                        mock.patch.object(C, "RULES_FILE", os.path.join(self.tmp, "rules.json"))]
        for p in self.patches:
            p.start()
        self.saved = (decide.DELETE_AT, decide.REVIEW_AT, decide.W_RULE)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        decide.DELETE_AT, decide.REVIEW_AT, decide.W_RULE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def f(self, *parts, data="x"):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write(path, data)
        return path

    def fitem(self, path, i=0, **kw):
        st = os.stat(path)
        it = {"id": i, "kind": "file", "path": path, "name": os.path.basename(path), "size": st.st_size,
              "mtime": st.st_mtime, "p": 0.9, "rec": "delete"}
        it.update(kw)
        return it


class ValidateTest(Tmp):
    def test_bad_entries_are_dropped_with_reasons(self):
        r, errors = rules.validate({"keep_dirs": ["relative\\x", self.tmp, self.tmp],
                                    "type_dest": {"安装包": r"C:\Windows\x", "不存在的类型": self.tmp, "文档": self.tmp},
                                    "settings": {"delete_at": 2, "review_at": 0.3, "w_rule": "abc"}})
        self.assertEqual(r["keep_dirs"], [os.path.normpath(self.tmp)])
        self.assertEqual(r["type_dest"], {"文档": os.path.normpath(self.tmp)})
        self.assertEqual(r["settings"]["delete_at"], rules.DEFAULT_SETTINGS["delete_at"])
        self.assertEqual(r["settings"]["w_rule"], rules.DEFAULT_SETTINGS["w_rule"])
        self.assertEqual(len(errors), 5)

    def test_review_must_be_below_delete(self):
        r, errors = rules.validate({"settings": {"delete_at": 0.6, "review_at": 0.7}})
        self.assertLess(r["settings"]["review_at"], r["settings"]["delete_at"])
        self.assertTrue(errors)

    def test_save_and_load(self):
        r, _ = rules.validate({"keep_dirs": [self.tmp]})
        rules.save(r)
        self.assertEqual(rules.load()["keep_dirs"], [os.path.normpath(self.tmp)])


class KeepAndIgnoreTest(Tmp):
    def test_kept_folder_hides_and_blocks_delete(self):
        path = self.f("论文资料", "big.iso")
        app = make_app({"items": [self.fitem(path)]})
        app.set_rules({"keep_dirs": [os.path.join(self.tmp, "论文资料")]})
        self.assertIn("永远保留", app.results_view()["items"][0]["hidden"])
        r = app.trash([0], "scan-1")
        self.assertIn("永远保留", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])

    def test_ignored_item_is_hidden_and_not_deleted(self):
        a, b = self.f("a.iso"), self.f("b.iso")
        app = make_app({"items": [self.fitem(a, 0), self.fitem(b, 1)]})
        self.assertTrue(app.ignore([0], "scan-1")["ok"])
        view = app.results_view()["items"]
        self.assertTrue(view[0]["hidden"])
        self.assertIsNone(view[1]["hidden"])
        self.assertEqual(rules.load()["ignored"][0]["name"], "a.iso")  # 记住了
        self.assertEqual(app.trash([0, 1], "scan-1")["done"], [1])
        self.assertTrue(app.ignore([1], "old")["stale"])  # 旧页面也不能改规则

    def test_group_ignored_by_name_across_scans(self):
        g = {"id": 0, "kind": "group", "name": "应用缓存 · Code", "path": r"C:\x\Code\logs", "paths": [r"C:\x\Code\logs"]}
        r = rules.defaults()
        r["ignored"].append({"key": rules.item_key(g), "name": g["name"], "path": g["path"]})
        other_scan = dict(g, path=r"C:\x\Code\Cache", paths=[r"C:\x\Code\Cache"])
        self.assertIsNotNone(rules.hidden_reason(other_scan, r))

    def test_duplicate_keeps_copy_in_kept_folder(self):
        data = os.urandom(200_000)
        files = []
        for rel in (("下载", "x.pdf"), ("资料", "x.pdf")):
            p = self.f(*rel, data=data)
            files.append({"path": p, "size": len(data), "mtime": os.path.getmtime(p), "cloud": False})
        with mock.patch.object(C, "APPDATA_NS", []):
            plain = dupes.find_duplicates(files, lambda *a: None, threading.Event())
            pref = dupes.find_duplicates(files, lambda *a: None, threading.Event(),
                                         prefer_dirs=[os.path.join(self.tmp, "资料")])
        self.assertIn("资料", pref[0]["keep"])
        self.assertEqual(len(plain), 1)


class ThresholdTest(Tmp):
    def test_lower_delete_line_changes_view_but_capped_stays_review(self):
        items = [dict(self.fitem(self.f("a.iso")), p=0.65, rec="review"),
                 dict(self.fitem(self.f("b.docx"), 1), p=0.6, rec="review", capped=True)]
        app = make_app({"items": items})
        app.set_rules({"settings": {"delete_at": 0.6, "review_at": 0.3, "w_rule": 0.45}})
        view = app.results_view()["items"]
        self.assertEqual(view[0]["rec"], "delete")   # 按新阈值重新分栏
        self.assertEqual(view[1]["rec"], "review")   # 个人资料/备份等永远最多到“需要你看”
        self.assertEqual(decide.W_RULE, 0.45)

    def test_rec_respects_cap(self):
        self.assertEqual(decide._rec(0.99, capped=True), "review")
        self.assertEqual(decide._rec(0.99), "delete")


class TypeDestTest(Tmp):
    def test_rule_destination_wins(self):
        dl = os.path.join(self.tmp, "下载")
        self.f("下载", "setup_x64.exe")
        self.f("下载", "notes.txt")
        dest = os.path.join(self.tmp, "软件安装包")
        with mock.patch.object(organize, "_forbidden_source", return_value=False):
            units, _ = organize.collect_units([dl], None)
        items = organize.Planner("type", None, type_dest={"安装包": dest}).plan(units, lambda *a: None, threading.Event())
        exe = next(i for i in items if i["name"] == "setup_x64.exe")
        txt = next(i for i in items if i["name"] == "notes.txt")
        self.assertEqual((exe["dest"], exe["via"]), (dest, "rule"))
        self.assertEqual(exe["options"][0]["path"], dest)
        self.assertEqual(txt["via"], "type")


class MigrateTest(unittest.TestCase):
    def test_old_results_move_once(self):
        tmp = tempfile.mkdtemp()
        try:
            old = os.path.join(tmp, "results")
            os.makedirs(os.path.join(old, "organize_history"))
            write(os.path.join(old, "last_scan.json"), "{}")
            new = os.path.join(tmp, "data")
            with mock.patch.object(C, "PROJECT_DIR", tmp), mock.patch.object(C, "DATA_DIR", new):
                self.assertTrue(C.migrate_old_data())
                self.assertTrue(os.path.exists(os.path.join(new, "last_scan.json")))
                self.assertFalse(C.migrate_old_data())  # 已经有新目录就不再动
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

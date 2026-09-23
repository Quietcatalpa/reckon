"""第一轮安全加固的测试：过期页面、疑似重复、备份与压缩包、缓存目录变化、软件目录、磁盘类型、解析限制。

    python -m unittest discover -s tests -v

全部在临时目录里进行，不会真的往回收站里放东西。
"""
import ctypes
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RECKON_DATA", tempfile.mkdtemp(prefix="reckon_test_"))

from reckon import config as C  # noqa: E402
from reckon import decide  # noqa: E402
from reckon import dupes  # noqa: E402
from reckon import extract  # noqa: E402
from reckon import organize  # noqa: E402
from reckon import recycle  # noqa: E402
from reckon import scanner  # noqa: E402
from reckon import server  # noqa: E402
from test_safety import make_app, write  # noqa: E402


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sent = []
        # 所有测试都不真的删文件：send2trash 换成记录，回收站检查默认放行
        self.patches = [mock.patch("send2trash.send2trash", side_effect=self.sent.append),
                        mock.patch.object(recycle, "check", return_value=None),
                        mock.patch.object(server.C, "RESULTS_FILE", os.path.join(self.tmp, "last_scan.json"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def f(self, *parts, data="x"):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write(path, data)
        return path

    def file_item(self, path, **kw):
        st = os.stat(path)
        it = {"id": 0, "kind": "file", "path": path, "name": os.path.basename(path), "size": st.st_size,
              "mtime": st.st_mtime}
        it.update(kw)
        return it


# 1. 旧页面、同时操作
class StaleAndBusyTest(TmpCase):
    def test_old_page_cannot_delete_after_rescan(self):
        app = make_app({"items": [self.file_item(self.f("a.iso"))], "scan_id": "new"})
        r = app.trash([0], "old")
        self.assertTrue(r["stale"])
        self.assertEqual(self.sent, [])

    def test_old_page_cannot_organize_after_new_plan(self):
        app = make_app(plan={"items": [], "plan_id": "new"})
        r = app.run_organize([{"id": 0, "dest": self.tmp}], "old")
        self.assertTrue(r["stale"])

    def test_operations_do_not_overlap(self):
        app = make_app({"items": [self.file_item(self.f("a.iso"))]})
        app.op_running = True  # 假装另一项删除/整理正在进行
        r = app.trash([0], "scan-1")
        self.assertIn("另一项操作正在进行", r["failed"][0]["error"])
        self.assertFalse(app._start("scan", lambda o: None, {}, ""))  # 也不能同时开始扫描
        app.op_running = False
        app.job = {"running": True}  # 扫描进行中也不能删
        self.assertIn("另一项操作正在进行", app.trash([0], "scan-1")["failed"][0]["error"])


# 2. 疑似重复
class DupeTest(TmpCase):
    def _pair(self, differ):
        """两个 1MB 文件，只在中间某处不同（抽样读不到的位置）。"""
        base = bytearray(os.urandom(1 << 20))
        a = self.f("keep", "big.bin", data=bytes(base))
        if differ:
            base[300_000] ^= 0xFF
        b = self.f("copy", "big.bin", data=bytes(base))
        return a, b

    def test_sampled_match_is_only_suspected(self):
        a, b = self._pair(differ=True)
        files = [{"path": p, "size": os.path.getsize(p), "mtime": os.path.getmtime(p), "cloud": False} for p in (a, b)]
        # 让“超大文件”的门槛变小、抽样块变小，模拟抽样没读到差异的情况；
        # 临时目录在 AppData 下，查重平时会跳过 AppData，这里放开
        with mock.patch.object(dupes, "FULL_HASH_LIMIT", 1024), mock.patch.object(dupes, "CHUNK", 1024), \
                mock.patch.object(C, "APPDATA_NS", []):
            groups = dupes.find_duplicates(files, lambda *a: None, threading.Event())
        self.assertEqual(len(groups), 1)
        self.assertFalse(groups[0]["exact"])
        scan = scanner.ScanResult()
        scan.files = files
        items, _ = decide.build_items(scan, groups, None, {"min_size_mb": 20}, lambda *a: None, threading.Event())
        dupe = next(i for i in items if i["kind"] == "dupe")
        self.assertEqual(dupe["rec"], "review")  # 不再进“建议删除”
        # 真要删的时候会完整比对，发现不同就拒绝
        app = make_app({"items": [dupe]})
        r = app.trash([dupe["id"]], "scan-1")
        self.assertIn("并不完全相同", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])

    def test_real_duplicate_is_deleted_after_full_compare(self):
        a, b = self._pair(differ=False)
        it = self.file_item(b, kind="dupe", dup_of=a, keep_mtime=os.path.getmtime(a))
        r = make_app({"items": [it]}).trash([0], "scan-1")
        self.assertEqual(r["done"], [0])
        self.assertEqual(self.sent, [b])

    def test_keep_modified_after_scan_blocks_delete(self):
        a, b = self._pair(differ=False)
        it = self.file_item(b, kind="dupe", dup_of=a, keep_mtime=os.path.getmtime(a) - 100)
        r = make_app({"items": [it]}).trash([0], "scan-1")
        self.assertIn("保留的那份在扫描后被修改过", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])


# 3. 备份和压缩包
class BackupArchiveTest(TmpCase):
    def test_backup_is_never_auto_delete(self):
        for folder in (C.DESKTOP, self.tmp):
            path = os.path.join(folder, "论文.docx.bak")
            p, reasons, _, capped = decide.rule_prior(path, C.norm(path), "备份", 500, "论文.docx.bak")
            self.assertTrue(capped)  # 封顶在“需要你看”
            self.assertLess(min(p, decide.PERSONAL_CAP), decide.DELETE_AT)
            self.assertTrue(any("备份" in r for r in reasons))
        self.assertEqual(C.file_type(".bak"), "备份")
        self.assertEqual(C.file_type(".old"), "备份")

    def _zip(self, name, entries, prefix=""):
        path = os.path.join(self.tmp, name)
        with zipfile.ZipFile(path, "w") as z:
            for rel, data in entries.items():
                z.writestr(prefix + rel, data)
        return path

    def test_archive_fully_extracted(self):
        entries = {"a.txt": "aaa", "sub/b.txt": "bb"}
        path = self._zip("课件.zip", entries)
        for rel, data in entries.items():
            self.f("课件", *rel.split("/"), data=data)
        self.assertEqual(decide.archive_status(path), ("full", 2))

    def test_archive_extracted_with_extra_top_folder(self):
        path = self._zip("资料.zip", {"a.txt": "aaa"}, prefix="资料/")
        self.f("资料", "a.txt", data="aaa")  # 解压时去掉了那一层同名文件夹
        self.assertEqual(decide.archive_status(path)[0], "full")

    def test_archive_partly_extracted_is_only_a_clue(self):
        path = self._zip("工具.zip", {"a.txt": "aaa", "b.txt": "bbb"})
        self.f("工具", "a.txt", data="aaa")  # b.txt 没解压出来
        self.assertEqual(decide.archive_status(path), ("folder", 0))
        p, reasons, _, _ = decide.rule_prior(path, C.norm(path), "压缩包", 10, "工具.zip")
        self.assertLess(p, 0.85)
        self.assertTrue(any("只作参考" in r for r in reasons))

    def test_rar_with_folder_is_only_a_clue(self):
        path = self.f("x.rar", data="rar")
        os.makedirs(os.path.join(self.tmp, "x"))
        self.assertEqual(decide.archive_status(path), ("folder", 0))


# 4. 缓存目录扫描后有变化
class StrictGroupTest(TmpCase):
    def _group(self, strict):
        d = os.path.join(self.tmp, "7zO123")
        self.f("7zO123", "old.tmp")
        stamp = scanner.dir_stats(d)[2]
        return d, {"id": 0, "kind": "group", "name": "系统临时文件", "path": d, "paths": [d], "stamps": [stamp],
                   "strict": strict, "size": 1, "mtime": stamp}

    def test_new_file_after_scan_blocks_strict_group(self):
        d, it = self._group(strict=True)
        time.sleep(1.2)
        self.f("7zO123", "new-after-scan.txt")
        r = make_app({"items": [it]}).trash([0], "scan-1")
        self.assertIn("有新的改动", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])

    def test_unchanged_strict_group_is_deleted(self):
        d, it = self._group(strict=True)
        r = make_app({"items": [it]}).trash([0], "scan-1")
        self.assertEqual(r["done"], [0])
        self.assertEqual(self.sent, [d])

    def test_app_cache_changes_are_fine(self):
        d, it = self._group(strict=False)  # 应用缓存本来就一直在变，随时能重建
        time.sleep(1.2)
        self.f("7zO123", "new.txt")
        self.assertEqual(make_app({"items": [it]}).trash([0], "scan-1")["done"], [0])


# 5. 软件目录不整理
class ProgramDirTest(TmpCase):
    def setUp(self):
        super().setUp()
        # 临时目录在 AppData 下面，平时会被整体禁止；这里放开，只测软件目录这条规则
        self.patches.append(mock.patch.object(organize, "_forbidden_source", return_value=False))
        self.patches[-1].start()
        self.prog = os.path.join(self.tmp, "SomeApp")
        for n in ("app.exe", "a.dll", "b.dll", "c.dll"):
            self.f("SomeApp", n)
        self.f("SomeApp", "data", "config.ini")

    def test_program_dir_itself_is_refused(self):
        units, notes = organize.collect_units([self.prog], None)
        self.assertEqual(units, [])
        self.assertIn("本身就是软件目录", notes[0])

    def test_folder_inside_program_dir_is_refused(self):
        units, notes = organize.collect_units([os.path.join(self.prog, "data")], None)
        self.assertEqual(units, [])
        self.assertIn("在软件目录", notes[0])

    def test_file_inside_program_dir_is_refused(self):
        units, notes = organize.collect_units([os.path.join(self.prog, "a.dll")], None)
        self.assertEqual(units, [])

    def test_installed_app_with_uninstaller(self):
        d = os.path.join(self.tmp, "Installed")
        self.f("Installed", "unins000.exe")
        self.f("Installed", "readme.txt")
        self.assertEqual(organize.collect_units([d], None)[0], [])

    def test_normal_folder_is_fine(self):
        self.f("下载", "a.pdf")
        units, notes = organize.collect_units([os.path.join(self.tmp, "下载")], None)
        self.assertEqual([u["name"] for u in units], ["a.pdf"])


# 8. 磁盘类型
class DrivesTest(unittest.TestCase):
    def test_types_and_skips(self):
        k32 = mock.Mock()
        k32.GetLogicalDrives.return_value = 0b111100  # C D E F
        k32.GetDriveTypeW.side_effect = lambda root: {"C:\\": 3, "D:\\": 2, "E:\\": 4, "F:\\": 5}[root]
        usage = mock.Mock(return_value=mock.Mock(total=100, used=40, free=60))
        with mock.patch.object(ctypes, "windll", mock.Mock(kernel32=k32)), mock.patch.object(server.shutil, "disk_usage", usage):
            drives = server.list_drives()
        self.assertEqual([(d["root"], d["type"]) for d in drives],
                         [("C:\\", "fixed"), ("D:\\", "removable"), ("E:\\", "network")])  # 光驱 F: 不列
        self.assertEqual(usage.call_count, 2)  # 网络盘不查容量


# 9. 解析限制
class ExtractLimitTest(TmpCase):
    def test_big_notebook_is_skipped(self):
        path = self.f("big.ipynb", data='{"cells": []}' + " " * 5000)
        with mock.patch.dict(extract.SIZE_LIMITS, {".ipynb": 1000}):
            self.assertIsNone(extract.snippet(path, ".ipynb"))

    def test_slow_parser_times_out(self):
        path = self.f("slow.txt", data="hello")

        def slow(p, limit):
            time.sleep(2)
            return "never"

        with mock.patch.object(extract, "TIMEOUT", 0.2), mock.patch.object(extract, "_text", slow):
            t = time.time()
            self.assertIsNone(extract.snippet(path, ".txt"))
            self.assertLess(time.time() - t, 1.5)


if __name__ == "__main__":
    unittest.main()

"""第四轮（外部审阅意见）的测试：保留/受保护目录的包含关系、跨盘移动失败后的撤销、
删除前核对成员清单、直接跟路径启动、单实例、压缩包逐个核对内容。

    python -m unittest discover -s tests -v

全部在临时目录里进行，不会真的往回收站里放东西。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RECKON_DATA", tempfile.mkdtemp(prefix="reckon_test_"))

from reckon import config as C  # noqa: E402
from reckon import decide  # noqa: E402
from reckon import organize  # noqa: E402
from reckon import scanner  # noqa: E402
from reckon import server  # noqa: E402
from test_round3 import Tmp  # noqa: E402
from test_safety import make_app  # noqa: E402


def group(path, stamp, strict=True, i=0):
    return {"id": i, "kind": "group", "name": "系统临时文件", "path": path, "paths": [path], "stamps": [stamp],
            "strict": strict, "size": 1, "mtime": time.time() - 86400 * 30, "p": 0.9, "rec": "delete"}


# 1. 目录里面包含“永远保留”的文件夹，删掉它也会连带删掉
class KeepContainmentTest(Tmp):
    def test_parent_of_kept_folder_is_hidden_and_not_deleted(self):
        job = os.path.join(self.tmp, "job")
        self.f("job", "saved", "important.docx")
        app = make_app({"items": [group(job, scanner.fingerprint(job))]})
        app.set_rules({"keep_dirs": [os.path.join(job, "saved")]})
        self.assertIn("里面有你设置的", app.results_view()["items"][0]["hidden"])
        r = app.trash([0], "scan-1")
        self.assertIn("永远保留", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])


# 2. 目录里面包含受保护的目录
class ContainsProtectedTest(Tmp):
    def test_parent_of_protected_dir_is_not_deleted(self):
        parent = os.path.join(self.tmp, "parent")
        self.f("parent", "guarded", "x.txt")
        guarded = C.norm(os.path.join(parent, "guarded"))
        with mock.patch.object(C, "PROTECTED_NS", C.PROTECTED_NS + [guarded]):
            self.assertTrue(C.contains_protected(C.norm(parent)))
            self.assertFalse(C.contains_protected(guarded))  # 它自己由 is_protected 管
            r = make_app({"items": [group(parent, scanner.fingerprint(parent))]}).trash([0], "scan-1")
        self.assertIn("受保护", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])


# 3. 跨盘移动做到一半失败：记录留着，撤销时把两边合并回原处
class PartialMoveTest(Tmp):
    def setUp(self):
        super().setUp()
        self.patches.append(mock.patch.object(organize, "HISTORY_DIR", os.path.join(self.tmp, "history")))
        self.patches[-1].start()
        self.src = os.path.join(self.tmp, "src", "项目")
        self.f("src", "项目", "a.txt", data="aaa")
        self.f("src", "项目", "b.txt", data="bbb")
        self.dest = os.path.join(self.tmp, "dest")
        self.items = [{"id": 0, "path": self.src, "name": "项目", "options": [{"path": self.dest, "label": "目标"}]}]

    def test_half_done_move_is_kept_and_undo_merges_back(self):
        def half_move(src, dst):
            shutil.copytree(src, dst)            # 复制完了……
            os.remove(os.path.join(src, "a.txt"))  # ……删原来的删到一半
            raise OSError("模拟删除原文件时失败")

        with mock.patch.object(organize.shutil, "move", side_effect=half_move):
            r = organize.execute(self.items, [{"id": 0, "dest": self.dest}])
        self.assertIn("移动没有完成", r["failed"][0]["error"])
        self.assertIsNotNone(r["history"])  # 记录没有被撤回

        u = organize.undo(r["history"])
        self.assertEqual((u["restored"], u["failed"]), (1, []))
        self.assertEqual(sorted(os.listdir(self.src)), ["a.txt", "b.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.dest, "项目")))

    def test_merge_back_does_not_overwrite_different_files(self):
        final = os.path.join(self.dest, "项目")
        shutil.copytree(self.src, final)
        with open(os.path.join(final, "b.txt"), "w", encoding="utf-8") as f:
            f.write("整理后又改过")
        self.assertEqual(organize._merge_back(final, self.src), 1)
        with open(os.path.join(self.src, "b.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "bbb")


# 4. 删除前核对成员清单
class FingerprintTest(Tmp):
    def setUp(self):
        super().setUp()
        self.d = os.path.join(self.tmp, "7zO123")
        self.f("7zO123", "old.tmp")

    def test_unchanged_is_deleted(self):
        r = make_app({"items": [group(self.d, scanner.fingerprint(self.d))]}).trash([0], "scan-1")
        self.assertEqual(r["done"], [0])

    def test_copied_in_old_file_is_noticed(self):
        """往里复制旧文件时修改时间不变，只看“最新修改时间”发现不了。"""
        stamp = scanner.fingerprint(self.d)
        old = self.f("7zO123", "copied-in.docx")
        t = time.time() - 86400 * 365
        os.utime(old, (t, t))
        r = make_app({"items": [group(self.d, stamp)]}).trash([0], "scan-1")
        self.assertIn("有变化", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])

    def test_unreadable_contents_are_skipped(self):
        stamp = dict(scanner.fingerprint(self.d), errors=1)
        r = make_app({"items": [group(self.d, stamp)]}).trash([0], "scan-1")
        self.assertIn("读不了", r["failed"][0]["error"])
        self.assertEqual(self.sent, [])

    def test_strict_groups_record_fingerprints(self):
        res = scanner.ScanResult()
        scanner._add_strict(res, "hc:x", "工具缓存", self.d, None, 0.45, "")
        scanner._add_group(res, "pycache", "编译缓存", self.d, 1, 1, 0.0, 0.9, "")
        self.assertEqual(res.groups["hc:x"]["stamps"][0], scanner.fingerprint(self.d))
        self.assertEqual(res.groups["hc:x"]["count"], 1)
        self.assertIsInstance(res.groups["pycache"]["stamps"][0], float)


# 5. 启动参数：直接跟路径（拖到 exe 上）等同于 --organize
class ArgsTest(unittest.TestCase):
    def test_bare_paths_mean_organize(self):
        a = server.parse_args([r"D:\下载", r"D:\桌面\杂物"])
        self.assertEqual(a.organize, [r"D:\下载", r"D:\桌面\杂物"])

    def test_no_paths_no_organize(self):
        self.assertIsNone(server.parse_args(["--demo"]).organize)

    def test_both_forms_combine(self):
        self.assertEqual(server.parse_args(["--organize", "a", "b"]).organize, ["a", "b"])


# 6. 同一时间只运行一个
class InstanceLockTest(unittest.TestCase):
    def tearDown(self):
        if server._instance_lock:
            server._instance_lock.close()
            server._instance_lock = None

    def test_second_instance_cannot_lock(self):
        path = os.path.join(tempfile.mkdtemp(), "instance.lock")
        self.assertTrue(server.acquire_instance_lock(path))
        first = server._instance_lock
        self.assertFalse(server.acquire_instance_lock(path))
        self.assertIs(server._instance_lock, first)  # 没拿到锁时不替换
        first.close()
        server._instance_lock = None
        self.assertTrue(server.acquire_instance_lock(path))  # 前一个退出后可以再拿

    def test_server_does_not_share_ports(self):
        self.assertFalse(server.Server.allow_reuse_address)


# 7. 压缩包：逐个核对内容
class ArchiveContentTest(Tmp):
    def _zip(self, name, entries):
        path = os.path.join(self.tmp, name)
        with zipfile.ZipFile(path, "w") as z:
            for rel, data in entries.items():
                z.writestr(rel, data)
        return path

    def test_same_name_and_size_but_different_content(self):
        path = self._zip("课件.zip", {"a.txt": "aaa"})
        self.f("课件", "a.txt", data="xyz")  # 大小一样、内容不同
        self.assertEqual(decide.archive_status(path), ("folder", 0))

    def test_too_big_to_verify_is_only_a_clue(self):
        path = self._zip("数据.zip", {"a.txt": "aaa", "b.txt": "bb"})
        self.f("数据", "a.txt", data="aaa")
        self.f("数据", "b.txt", data="bb")
        self.assertEqual(decide.archive_status(path, max_verify_bytes=1), ("size", 2))
        with mock.patch.object(decide, "archive_status", return_value=("size", 2)):
            p, reasons, _, _ = decide.rule_prior(path, C.norm(path), "压缩包", 10, "数据.zip")
        self.assertLess(p, 0.85)
        self.assertTrue(any("没有逐个核对内容" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()

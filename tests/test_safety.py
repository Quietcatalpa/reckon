"""安全相关的测试：回收站检查、整理记录防崩溃、撤销可重试。

    python -m unittest discover -s tests -v

测试只在临时目录里造文件，不会真的往回收站里放东西。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import organize  # noqa: E402
import recycle  # noqa: E402
import server  # noqa: E402


def write(path, data):
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, **({} if mode == "wb" else {"encoding": "utf-8"})) as f:
        f.write(data)


class RecycleCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "a.txt")
        write(self.path, "x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_small_file_is_ok(self):
        self.assertIsNone(recycle.check(self.path, 1))

    def test_bigger_than_bin_is_refused(self):
        why = recycle.check(self.path, 10 ** 15)
        self.assertIn("容量上限", why)

    def test_bin_disabled_is_refused(self):
        with mock.patch.object(recycle, "bin_settings", return_value=(False, 10 ** 12)):
            self.assertIn("直接删除", recycle.check(self.path, 1))

    def test_non_fixed_drive_is_refused(self):
        fake = mock.Mock()
        fake.GetDriveTypeW.return_value = 4  # 网络盘
        with mock.patch.object(recycle, "volume_root", return_value="Z:\\"), mock.patch.object(recycle, "_k32", fake):
            self.assertIn("不是本机硬盘", recycle.check(self.path, 1))


class TrashTest(unittest.TestCase):
    """App.trash 放不进回收站时必须拒绝，并且文件原样保留。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "big.iso")
        write(self.path, b"0" * 100)
        st = os.stat(self.path)
        self.app = server.App.__new__(server.App)
        self.app.demo = False
        self.app.results = {"items": [{"id": 0, "kind": "file", "path": self.path, "size": st.st_size,
                                       "mtime": st.st_mtime, "name": "big.iso"}]}
        self.results_file = os.path.join(self.tmp, "last_scan.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refuses_when_bin_too_small(self):
        sent = []
        with mock.patch.object(recycle, "check", return_value="超过了回收站的容量上限"), \
                mock.patch("send2trash.send2trash", side_effect=sent.append), \
                mock.patch.object(server.C, "RESULTS_FILE", self.results_file):
            r = self.app.trash([0])
        self.assertEqual(r["done"], [])
        self.assertIn("容量上限", r["failed"][0]["error"])
        self.assertEqual(sent, [])
        self.assertTrue(os.path.exists(self.path))

    def test_sends_when_ok(self):
        sent = []
        with mock.patch.object(recycle, "check", return_value=None), \
                mock.patch("send2trash.send2trash", side_effect=sent.append), \
                mock.patch.object(server.C, "RESULTS_FILE", self.results_file):
            r = self.app.trash([0])
        self.assertEqual(r["done"], [0])
        self.assertEqual(sent, [self.path])


class OrganizeHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src_dir = os.path.join(self.tmp, "下载")
        self.dest = os.path.join(self.tmp, "资料", "新分类")  # 不存在，整理时会新建
        os.makedirs(self.src_dir)
        self.items = []
        for i, name in enumerate(["a.txt", "b.txt", "c.txt"]):
            p = os.path.join(self.src_dir, name)
            write(p, name)
            self.items.append({"id": i, "path": p, "name": name, "options": [{"path": self.dest, "label": "新分类"}]})
        self.hist = mock.patch.object(organize, "HISTORY_DIR", os.path.join(self.tmp, "history"))
        self.hist.start()

    def tearDown(self):
        self.hist.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _moves(self):
        return [{"id": it["id"], "dest": self.dest} for it in self.items]

    def _history(self, hid):
        with open(os.path.join(organize.HISTORY_DIR, hid + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def test_record_written_before_each_move(self):
        """第 2 次移动时“崩溃”：第 1 项已经在记录里，第 2 项的记录被撤回。"""
        real_move = shutil.move
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) == 2:
                raise OSError("模拟移动失败")
            return real_move(src, dst)

        with mock.patch.object(organize.shutil, "move", side_effect=flaky):
            r = organize.execute(self.items, self._moves())
        self.assertEqual(r["done"], [0, 2])
        h = self._history(r["history"])
        self.assertEqual([os.path.basename(x["src"]) for x in h["records"]], ["a.txt", "c.txt"])
        self.assertTrue(os.path.exists(self.items[1]["path"]))  # 失败的那项还在原处

    def test_record_survives_crash_right_after_saving(self):
        """记录已写、文件还没来得及移动就崩溃了：撤销时应当认为它已经在原处。"""
        with mock.patch.object(organize.shutil, "move", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                organize.execute(self.items[:1], self._moves()[:1])
        hid = organize.list_history()[0]["id"]
        u = organize.undo(hid)
        self.assertEqual(u["failed"], [])
        self.assertTrue(self._history(hid)["undone"])

    def test_partial_undo_can_be_retried(self):
        r = organize.execute(self.items, self._moves())
        self.assertEqual(len(r["done"]), 3)
        blocker = self.items[0]["path"]
        write(blocker, "新文件占了原位置")  # 让 a.txt 放不回去
        u = organize.undo(r["history"])
        self.assertEqual((u["restored"], len(u["failed"]), u["left"]), (2, 1, 1))
        info = organize.list_history()[0]
        self.assertFalse(info["undone"])
        self.assertTrue(info["partial"])
        self.assertTrue(os.path.isdir(self.dest))  # 还有东西没放回，新建的文件夹先不删

        os.remove(blocker)
        u = organize.undo(r["history"])
        self.assertEqual((u["restored"], u["failed"], u["left"]), (1, [], 0))
        info = organize.list_history()[0]
        self.assertTrue(info["undone"])
        self.assertEqual(info["sample"], ["a.txt", "b.txt", "c.txt"])  # 撤销完了记录里仍显示移动过哪些
        self.assertFalse(os.path.exists(self.dest))  # 全部放回后删掉整理时新建的空文件夹
        for it in self.items:
            self.assertTrue(os.path.exists(it["path"]))


if __name__ == "__main__":
    unittest.main()

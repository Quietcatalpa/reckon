"""第二轮的测试：模型判断的缓存与优先级、并行查重、必然是垃圾的文件、评估集回归。

    python -m unittest discover -s tests -v

不需要 Laya 模型：用一个假的判断器代替。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "eval"))

import config as C  # noqa: E402
import decide  # noqa: E402
import dupes  # noqa: E402
from test_safety import write  # noqa: E402


class FakeJudge:
    status = "ready"
    model_id = "fake"

    def __init__(self, delete=0.9):
        self.calls, self.delete = [], delete

    def judge(self, state, with_topic):
        self.calls.append(state["文件名"])
        return {"delete": self.delete}


def item(name, size, rule, mtime=1000.0, path=None):
    return {"name": name, "path": path or os.path.join(r"D:\x", name), "size": size, "mtime": mtime, "type": "文档",
            "age": 10, "reasons": [], "_rule": rule, "_capped": False, "_ext": ".pdf", "_cloud": True, "p": rule}


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.file = os.path.join(self.tmp, "laya_cache.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unchanged_file_reuses_judgment(self):
        a = item("a.pdf", 100, 0.5)
        c = decide.JudgmentCache(self.file, "v1")
        c.put(a, 0.8, True)
        c.save()
        c2 = decide.JudgmentCache(self.file, "v1")
        self.assertEqual(c2.get(a)["delete"], 0.8)
        changed = dict(a, mtime=2000.0)
        self.assertIsNone(decide.JudgmentCache(self.file, "v1").get(changed))  # 改过就重新判断
        self.assertIsNone(decide.JudgmentCache(self.file, "v2").get(a))  # 问题或模型变了，旧记录作废

    def test_only_files_seen_this_scan_are_kept(self):
        c = decide.JudgmentCache(self.file, "v1")
        c.put(item("a.pdf", 1, 0.5), 0.1, False)
        c.put(item("b.pdf", 1, 0.5), 0.2, False)
        c.save()
        c2 = decide.JudgmentCache(self.file, "v1")
        c2.get(item("a.pdf", 1, 0.5))  # 这次只扫到 a
        c2.save()
        c3 = decide.JudgmentCache(self.file, "v1")
        self.assertIsNotNone(c3.get(item("a.pdf", 1, 0.5)))
        self.assertIsNone(c3.get(item("b.pdf", 1, 0.5)))

    def test_version_depends_on_questions_and_model(self):
        j = FakeJudge()
        v = decide.cache_version(j)
        j.model_id = "other-model"
        self.assertNotEqual(v, decide.cache_version(j))


class JudgeAllTest(unittest.TestCase):
    def run_all(self, items, limit=None, cache=None, stop=None):
        j = FakeJudge()
        decide._judge_all(items, j, limit, cache, lambda *a: None, stop or threading.Event())
        return j

    def test_priority_uncertain_first_then_rule_delete_then_keep(self):
        items = [item("keep_big.pdf", 900, 0.2), item("sure_delete.pdf", 500, 0.9),
                 item("unsure_small.pdf", 10, 0.5), item("unsure_big.pdf", 800, 0.6)]
        j = self.run_all(items)
        self.assertEqual(j.calls, ["unsure_big.pdf", "unsure_small.pdf", "sure_delete.pdf", "keep_big.pdf"])

    def test_no_limit_by_default_and_limit_when_given(self):
        items = [item(f"{i}.pdf", i, 0.5) for i in range(20)]
        self.assertEqual(len(self.run_all(items).calls), 20)
        items = [item(f"{i}.pdf", i, 0.5) for i in range(20)]
        self.assertEqual(len(self.run_all(items, limit=5).calls), 5)
        self.assertTrue(any("超出本次设置" in r for it in items for r in it["reasons"]))

    def test_cached_items_are_not_judged_again(self):
        tmp = tempfile.mkdtemp()
        try:
            cache = decide.JudgmentCache(os.path.join(tmp, "c.json"), "v1")
            a, b = item("a.pdf", 1, 0.5), item("b.pdf", 2, 0.5)
            cache.put(a, 0.3, True)
            cache.old, cache.new = cache.new, {}
            j = self.run_all([a, b], cache=cache)
            self.assertEqual(j.calls, ["b.pdf"])
            self.assertTrue(a["laya"]["cached"])
            self.assertFalse(b["laya"]["cached"])
            self.assertEqual(round(a["p"], 3), round(decide.fuse(0.5, 0.3, False), 3))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stop_stops_judging(self):
        stop = threading.Event()
        stop.set()
        self.assertEqual(self.run_all([item("a.pdf", 1, 0.5)], stop=stop).calls, [])


class SureJunkTest(unittest.TestCase):
    def test_incomplete_download_and_dump_skip_model(self):
        for name in ("电影.mkv.crdownload", "数据.zip.part", "memory.dmp"):
            path = os.path.join(C.DOWNLOADS, name)
            p, reasons, skip_model, capped = decide.rule_prior(path, C.norm(path), C.file_type(os.path.splitext(name)[1]),
                                                               100, name.lower())
            self.assertTrue(skip_model, name)
            self.assertGreaterEqual(p, decide.DELETE_AT, name)

    def test_logs_still_go_to_model(self):
        path = os.path.join(C.DOCUMENTS, "train.log")
        _, _, skip_model, _ = decide.rule_prior(path, C.norm(path), "日志缓存", 100, "train.log")
        self.assertFalse(skip_model)  # 训练日志、实验记录可能有用，交给模型一起判断


class ParallelDupesTest(unittest.TestCase):
    def test_same_groups_as_brute_force(self):
        tmp = tempfile.mkdtemp()
        try:
            files = []

            def add(name, data):
                p = os.path.join(tmp, name)
                write(p, data)
                files.append({"path": p, "size": len(data), "mtime": os.path.getmtime(p), "cloud": False})

            base = os.urandom(300_000)
            for i in range(3):
                add(f"copy{i}.bin", base)             # 三份完全相同
            add("near.bin", base[:-1] + b"\x00")       # 大小相同、最后一字节不同
            add("other.bin", os.urandom(300_000))      # 大小相同、内容不同
            add("small_a.txt", b"hello" * 1000)
            add("small_b.txt", b"hello" * 1000)
            from unittest import mock
            with mock.patch.object(C, "APPDATA_NS", []):  # 临时目录在 AppData 下，平时查重会跳过
                groups = dupes.find_duplicates(files, lambda *a: None, threading.Event())
            got = sorted(sorted([g["keep"]] + g["copies"]) for g in groups)
            by = defaultdict(list)
            for f in files:
                with open(f["path"], "rb") as fh:
                    by[hashlib.sha256(fh.read()).hexdigest()].append(f["path"])
            want = sorted(sorted(v) for v in by.values() if len(v) > 1)
            self.assertEqual(got, want)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EvalRegressionTest(unittest.TestCase):
    """评估集上只用规则（GitHub 上没有模型）也不能出现“该留的被建议删除”。"""

    def test_rules_only_has_no_dangerous_errors(self):
        import run_eval
        cases = run_eval.load_cases()
        preps = [run_eval.prepare(c) for c in cases]
        _, conf, metrics = run_eval.score(cases, preps, [None] * len(cases), decide.W_RULE)
        self.assertEqual(metrics["危险(该留→删)"], 0)
        self.assertGreaterEqual(sum(conf[(a, a)] for a in run_eval.LABELS), 40)  # 防止规则改动后明显变差


if __name__ == "__main__":
    unittest.main()

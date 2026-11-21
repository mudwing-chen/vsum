#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P3 batch：清单解析、目录名、跳过已完成、失败不中断、batch.json 原子落盘。"""
import json
import os
import subprocess
import sys

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import batch  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


work = mkdtemp()

# 清单
txt = os.path.join(work, "urls.txt")
with open(txt, "w", encoding="utf-8") as fh:
    fh.write("# 注释\nhttps://www.douyin.com/video/7300000000000000001\n\n"
             "https://www.bilibili.com/video/BV1xx411c7mD\n"
             "https://www.douyin.com/video/7300000000000000001\n")
urls = batch.load_list(txt)
check("txt 去重保序", len(urls) == 2 and urls[0].endswith("001"), urls)
idx = os.path.join(work, "index.json")
with open(idx, "w", encoding="utf-8") as fh:
    json.dump({"items": [{"href": "https://youtu.be/abcdefghijk"}, {"href": ""}]}, fh)
check("index.json 读 href", batch.load_list(idx) == ["https://youtu.be/abcdefghijk"], batch.load_list(idx))

# 目录名
check("抖音目录名", batch.slug("https://www.douyin.com/video/7300000000000000001") ==
      "douyin-7300000000000000001")
check("B站目录名", batch.slug("https://www.bilibili.com/video/BV1xx411c7mD") ==
      "bilibili-BV1xx411c7mD")
check("YouTube 目录名", batch.slug("https://www.youtube.com/watch?v=abcdefghijk") ==
      "youtube-abcdefghijk")
check("未知链接用 sha1", batch.slug("https://example.com/x").startswith("other-") and
      len(batch.slug("https://example.com/x")) == len("other-") + 10)

# 假 pipeline：第一条成功，第二条失败
calls = []


def fake(url, out_dir, *a, **k):
    calls.append(url)
    os.makedirs(out_dir, exist_ok=True)
    if "fail" in url:
        raise RuntimeError("boom")
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump({"ok": True}, fh)
    return {"ok": True}


real = batch.pipeline.run
batch.pipeline.run = fake
out = os.path.join(work, "out")
lst = os.path.join(work, "mixed.txt")
with open(lst, "w", encoding="utf-8") as fh:
    fh.write("https://www.bilibili.com/video/BV1xx411c7mD\n"
             "https://www.bilibili.com/video/BVfail411c7m\n"
             "https://youtu.be/abcdefghijk\n")
code = batch.run(lst, out, model="tiny")
check("有失败退出 5", code == 5, code)
data = json.load(open(os.path.join(out, "batch.json"), encoding="utf-8"))
check("batch.json 计数", data["counts"] == {"ok": 2, "skipped": 0, "failed": 1}, data["counts"])
check("失败也继续跑完", len(calls) == 3, calls)
check("失败项有错误", any(i["status"] == "failed" and "boom" in i["error"] for i in data["items"]), data["items"])
check("没有 .tmp 残留", not os.path.exists(os.path.join(out, "batch.json.tmp")))

# 第二次：完成的跳过，失败的重跑
calls.clear()
code = batch.run(lst, out, model="tiny")
check("重跑只跑失败项", len(calls) == 1 and "fail" in calls[0], calls)
data = json.load(open(os.path.join(out, "batch.json"), encoding="utf-8"))
check("跳过计数", data["counts"]["skipped"] == 2, data["counts"])

# --force 全部重跑
calls.clear()
batch.run(lst, out, model="tiny", force=True)
check("--force 全跑", len(calls) == 3, calls)
batch.pipeline.run = real

# CLI：空清单 exit 0 且写 batch.json
empty = os.path.join(work, "empty.txt")
open(empty, "w").close()
proc = subprocess.run([sys.executable, "-m", "vsum", "batch", empty, "-o",
                       os.path.join(work, "out2")], cwd=ROOT, capture_output=True,
                      text=True, encoding="utf-8", errors="replace")
check("空清单退出 0", proc.returncode == 0, proc.stderr[-200:])
check("空清单也写 batch.json", os.path.exists(os.path.join(work, "out2", "batch.json")))
proc = subprocess.run([sys.executable, "-m", "vsum", "batch", os.path.join(work, "nope.txt"),
                       "-o", os.path.join(work, "out3")], cwd=ROOT, capture_output=True,
                      text=True, encoding="utf-8", errors="replace")
check("清单不存在退出 2", proc.returncode == 2 and "error:" in proc.stderr, proc.returncode)

if FAIL:
    print("FAIL\n" + "\n".join(FAIL))
    sys.exit(1)
print("OK batch")
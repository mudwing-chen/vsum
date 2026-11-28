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
check("batch.json 计数", data["counts"] == {"ok": 2, "skipped": 0, "failed": 1,
                                          "interrupted": 0, "pending": 0}, data["counts"])
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

# 失败的空目录不该留在产物根目录里（以前并行还会留一个只有 .batch.log 的目录）
def fake_dir_then_boom(url, target, *a, **k):
    os.makedirs(target, exist_ok=True)
    raise RuntimeError("boom-after-mkdir")


batch.pipeline.run = fake_dir_then_boom
empty_fail_out = os.path.join(work, "out_emptyfail")
empty_fail_lst = os.path.join(work, "one.txt")
with open(empty_fail_lst, "w", encoding="utf-8") as fh:
    fh.write("https://www.bilibili.com/video/BV1cc411c7m3\n")
batch.run(empty_fail_lst, empty_fail_out, model="tiny")
check("失败后清掉空目录",
      not os.path.exists(os.path.join(empty_fail_out, "bilibili-BV1cc411c7m3")),
      os.listdir(empty_fail_out))
batch.pipeline.run = real

# --jobs 并行：走真 Popen 路径（把子进程换成必定失败的 python，验证调度、日志、计数）
real_child_cmd = batch._child_cmd
batch._child_cmd = lambda *a, **k: [sys.executable, "-c", "import sys; sys.exit(2)"]
par_out = os.path.join(work, "par")
par_list = os.path.join(work, "par.txt")
with open(par_list, "w", encoding="utf-8") as fh:
    fh.write("https://www.bilibili.com/video/BV1aa411c7m1\n"
             "https://www.bilibili.com/video/BV1bb411c7m2\n")
par_code = batch.run(par_list, par_out, model="tiny", jobs=2)
batch._child_cmd = real_child_cmd
par = json.load(open(os.path.join(par_out, "batch.json"), encoding="utf-8"))
check("并行有失败退出 5", par_code == 5, par_code)
check("并行 counts 与 items 对得上",
      sum(par["counts"].values()) == par["total"] == len(par["items"]) == 2, par["counts"])
check("并行失败项都记 failed", par["counts"]["failed"] == 2, par["counts"])
check("并行日志放 .logs/ 且写进 error",
      all(".logs/" in i["error"] for i in par["items"])
      and len(os.listdir(os.path.join(par_out, ".logs"))) == 2, par["items"])
check("并行失败不留目标目录和 .batch.log",
      not os.path.exists(os.path.join(par_out, "bilibili-BV1aa411c7m1"))
      and not any(n.endswith(".batch.log") for n in os.listdir(par_out)), os.listdir(par_out))

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
# --devices：裸数字按 CUDA 序号理解（nvidia-smi 的写法），不合法就早报错
check("devices 裸数字", batch._device_list("0,1") == ["cuda:0", "cuda:1"], batch._device_list("0,1"))
check("devices 保留显式写法", batch._device_list("cuda:0, cpu") == ["cuda:0", "cpu"])
try:
    batch._device_list("0.5")
    check("devices 非法要报错", False)
except ValueError:
    check("devices 非法要报错", True)

print("OK batch")
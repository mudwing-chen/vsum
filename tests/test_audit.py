#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语体检的回归：型号/版本号写法不能把 I'm 3 分钟 当成 m-3，也不能漏掉真写法。"""
import json
import os
import sys

from _tmpdir import mkdtemp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import audit  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


work = mkdtemp()
for vid, lines in {
    "vid-a": ["I'm 3 分钟就写完了", "我一直在用 GPT 4 和 Claude 3.5", "iPhone 15 上的 M3 芯片"],
    "vid-b": ["这个 v2ray 配置和 m3u8 地址", "版本号 v2 和 sora 1"],
}.items():
    d = os.path.join(work, vid)
    os.makedirs(d)
    with open(os.path.join(d, "transcript.txt"), "w", encoding="utf-8") as fh:
        fh.write("".join("[%6.1f -> %6.1f] %s\n" % (i * 5.0, i * 5.0 + 4.0, ln)
                         for i, ln in enumerate(lines)))

md_path = os.path.join(work, "term-audit.md")
json_path = os.path.join(work, "term-audit.json")
rc = audit.run(work, md_path, json_path)
data = json.load(open(json_path, encoding="utf-8"))
found = [s["found"] for s in data["shape"]]
suggest = [s["suggest"].lower() for s in data["shape"]]

check("audit 退出 0", rc == 0, rc)
check("audit 写了 md 与 json",
      os.path.exists(md_path) and os.path.exists(json_path), (md_path, json_path))
check("收缩式 I'm 3 不算型号", not any(f.lower().startswith("m 3") or f.lower() == "m 3"
                                       for f in found), found)
check("缩写里的小写 m 3 不被抓", not any(f.replace("'", "").lower() in ("m3", "m 3")
                                         for f in found if "'" in f), found)
check("GPT 4 命中", "gpt-4" in suggest, suggest)
check("iPhone 15 命中", "iphone-15" in suggest, suggest)
check("M3 命中", "m-3" in suggest, suggest)
check("v2ray 不算 v-2", not any("v2ray" in s["found"] for s in data["shape"]),
      [s["found"] for s in data["shape"]])
check("m3u8 不算 m-3", not any("m3u8" in s["found"] for s in data["shape"]),
      [s["found"] for s in data["shape"]])
check("sora 1 命中", "sora-1" in suggest, suggest)
check("报告里有分节", "## 三、版本号/型号写法异常" in open(md_path, encoding="utf-8").read())

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK audit")

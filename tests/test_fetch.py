#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取层回归：URL 识别、id 解析、dry-run、错误退出码、play.json。不联网、不起浏览器。"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import browser, fetch  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append("%s %s" % (name, detail))


def run(*argv):
    return subprocess.run([sys.executable, "-m", "vsum"] + list(argv), cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


check("识别抖音", fetch.detect("https://v.douyin.com/abc/") == "douyin")
check("识别 B站", fetch.detect("https://www.bilibili.com/video/BV1xx") == "bilibili")
check("识别短链 b23.tv", fetch.detect("https://b23.tv/abc") == "bilibili")
check("识别 YouTube", fetch.detect("https://youtu.be/abc") == "youtube")
check("不认识 -> None", fetch.detect("https://example.com/x") is None)

check("从 /video/ 取 id", fetch.aweme_id_of("https://www.douyin.com/video/7300000000000000001") == "7300000000000000001")
check("从 modal_id 取 id", fetch.aweme_id_of("https://www.douyin.com/?modal_id=7300000000000000002") == "7300000000000000002")
check("短链取不到 id", fetch.aweme_id_of("https://v.douyin.com/abc/") is None)

check("Safari 提示含 safaridriver", "safaridriver" in browser.safari_hint())
check("Safari 提示含「允许远程自动化」", "允许远程自动化" in browser.safari_hint())

tmp = os.path.join(HERE, "_tmp_fetch")
os.makedirs(tmp, exist_ok=True)
out = os.path.join(tmp, "out")

try:
    r = run("fetch", "https://v.douyin.com/abc123/", "-o", out, "--dry-run")
    check("dry-run 退出 0", r.returncode == 0, (r.returncode, r.stderr))
    check("dry-run 打印计划", "计划" in r.stdout, r.stdout)
    check("dry-run 不建目录", not os.path.isdir(out), out)

    r2 = run("fetch", "https://example.com/nope", "-o", out)
    check("不认识的链接退出 4", r2.returncode == 4, (r2.returncode, r2.stderr))
    check("错误走 stderr 且无 traceback", "Traceback" not in r2.stderr and r2.stderr.startswith("error:"), r2.stderr)

    r3 = run("fetch", "https://v.douyin.com/abc/", "-o", out, "--dry-run", "--browser", "chrome")
    check("能强制指定浏览器", r3.returncode == 0 and "chrome" in r3.stdout, (r3.returncode, r3.stdout, r3.stderr))

    r4 = run("fetch", "https://v.douyin.com/abc/", "-o", out, "--dry-run", "--browser", "edge", "--headless", "--profile", "/tmp/vsum-p")
    check("edge + headless + profile 参数可达", r4.returncode == 0 and "edge" in r4.stdout, (r4.returncode, r4.stdout, r4.stderr))
    check("dry-run 不建 profile 目录", not os.path.isdir("/tmp/vsum-p"), "")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# yt-dlp 路径也要写 play.json（v0.3 说「仅链接输入」，不只抖音）
tmp_yt = os.path.join(HERE, "_tmp_fetch_yt")
shutil.rmtree(tmp_yt, ignore_errors=True)
os.makedirs(tmp_yt, exist_ok=True)
_orig = fetch.ytdlp


def _fake_ytdlp(url, out_dir, dry_run=False):
    path = os.path.join(out_dir, "video.mp4")
    with open(path, "wb") as fh:
        fh.write(b"x" * 32)
    return {"ok": True, "kind": "bilibili", "url": url, "title": "t",
            "durationSec": 2.0, "video": path}


try:
    fetch.ytdlp = _fake_ytdlp
    fetch.run("https://www.bilibili.com/video/BV1xx", tmp_yt)
    pj = json.load(open(os.path.join(tmp_yt, "play.json"), encoding="utf-8"))
    check("yt-dlp 写 play.json", pj.get("url", "").endswith("BV1xx"), pj)
    check("play.json 记 videoBytes", pj.get("videoBytes") == 32, pj)
    check("play.json 含 probeSec 键", "probeSec" in pj, pj)
finally:
    fetch.ytdlp = _orig
    shutil.rmtree(tmp_yt, ignore_errors=True)

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK fetch (19 checks)")

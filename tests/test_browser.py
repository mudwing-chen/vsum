#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浏览器层纯函数回归：profile 解析、平台路径、参数校验。不装 selenium、不起浏览器。"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import browser  # noqa: E402

FAIL = []
N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    if not cond:
        FAIL.append("%s %s" % (name, detail))


check("默认 profile 指向平台默认目录",
      browser.profile_dir() == os.path.abspath(browser.default_profile()), browser.profile_dir())
if sys.platform != "win32":
    check("非 Windows 默认 profile 在 ~/.cache/vsum/chrome",
          browser.default_profile() == os.path.join(os.path.expanduser("~"), ".cache", "vsum", "chrome"))

old = os.environ.get("VSUM_BROWSER_PROFILE")
try:
    # profile_dir 返回 abspath(expanduser(...))，断言也得先规范化：
    # 直接比 expanduser 的原始串在 Windows 上会因为 "C:\a/b" 和 "C:\a\b" 而假红。
    os.environ["VSUM_BROWSER_PROFILE"] = "~/tmp/vsum-profile"
    want = os.path.abspath(os.path.expanduser("~/tmp/vsum-profile"))
    check("VSUM_BROWSER_PROFILE 生效", browser.profile_dir() == want, browser.profile_dir())
    want2 = os.path.abspath("/tmp/absolute-p")
    check("--profile 优先级最高", browser.profile_dir("/tmp/absolute-p") == want2, browser.profile_dir("/tmp/absolute-p"))
finally:
    if old is None:
        os.environ.pop("VSUM_BROWSER_PROFILE", None)
    else:
        os.environ["VSUM_BROWSER_PROFILE"] = old

win_p = browser.default_profile({"LOCALAPPDATA": r"C:\LOCAL"}, "Windows")
check("Windows 默认 profile 用 LOCALAPPDATA",
      win_p == os.path.join(r"C:\LOCAL", "vsum", "chrome"), win_p)
fallback = browser.default_profile({}, "Windows")
check("LOCALAPPDATA 缺失时退 AppData/Local",
      fallback.endswith(os.path.join("vsum", "chrome")) and "AppData" in fallback, fallback)

fake_env = {"ProgramFiles": r"C:\PF", "ProgramFiles(x86)": r"C:\PF86", "LOCALAPPDATA": r"C:\LOCAL"}
win = browser.windows_candidates(fake_env)
check("Windows 三个根 × 两个浏览器 = 6 条", len(win) == 6, len(win))
check("Windows chrome 路径", ("chrome", os.path.join(r"C:\PF", "Google", "Chrome", "Application", "chrome.exe")) in win)
check("Windows edge 路径", ("edge", os.path.join(r"C:\LOCAL", "Microsoft", "Edge", "Application", "msedge.exe")) in win)
check("Windows 候选不含 None", all(p for _, p in win))
check("空 env 不炸", browser.windows_candidates({}) == [])

mac = browser.candidates("Darwin")
check("macOS 候选里有 Edge", any(k == "edge" and "Microsoft Edge" in p for k, p in mac), mac)
check("macOS Chrome 在前", mac[0][0] == "chrome", mac[0])
check("Linux 候选非空", len(browser.candidates("Linux")) >= 3)

tmp = tempfile.mkdtemp(prefix="vsum-browser-")
try:
    pf = os.path.join(tmp, "PF")
    os.makedirs(os.path.join(pf, "Microsoft", "Edge", "Application"))
    edge_exe = os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe")
    open(edge_exe, "w").close()
    check("find_binary 找到 Edge", browser.find_binary("edge", "Windows", {"ProgramFiles": pf}) == edge_exe)
    check("find_binary 找不到 chrome 返回 None",
          browser.find_binary("chrome", "Windows", {"ProgramFiles": pf}) in (None, shutil.which("chrome")))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

try:
    browser.open_browser("opera")
    check("非法浏览器名报 BrowserError", False)
except browser.BrowserError as exc:
    check("非法浏览器名报 BrowserError", "opera" in str(exc), str(exc))
except ImportError:
    check("非法浏览器名先校验再 import selenium", False, "ImportError 先发生了")

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK browser (%d checks)" % N[0])

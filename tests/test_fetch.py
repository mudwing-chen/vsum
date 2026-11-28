#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取层回归：URL 识别、id 解析、dry-run、错误退出码、play.json、原子下载、复用。不联网、不起浏览器。"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import urllib.request
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from vsum import browser, fetch  # noqa: E402

FAIL = []
N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    if not cond:
        FAIL.append("%s %s" % (name, detail))


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def run(*argv):
    return subprocess.run([sys.executable, "-m", "vsum"] + list(argv), cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


check("识别抖音", fetch.detect("https://v.douyin.com/abc/") == "douyin")
check("识别 B站", fetch.detect("https://www.bilibili.com/video/BV1xx") == "bilibili")
check("识别短链 b23.tv", fetch.detect("https://b23.tv/abc") == "bilibili")
check("识别 YouTube", fetch.detect("https://youtu.be/abc") == "youtube")
check("不认识 -> None", fetch.detect("https://example.com/x") is None)

# 只认「host 等于域名或它的子域」，不能子串匹配
check("骗子域名不算 YouTube", fetch.detect("notyoutube.com") is None, fetch.detect("notyoutube.com"))
check("带 www 的 YouTube 正常认",
      fetch.detect("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube",
      fetch.detect("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
check("子域名也算（m.bilibili.com）",
      fetch.detect("https://m.bilibili.com/video/BV1xx") == "bilibili",
      fetch.detect("https://m.bilibili.com/video/BV1xx"))
check("路径里出现域名不算",
      fetch.detect("https://example.com/youtube.com/watch") is None,
      fetch.detect("https://example.com/youtube.com/watch"))
check("相似域名不算（youtube.com.evil.cn）",
      fetch.detect("https://youtube.com.evil.cn/watch") is None,
      fetch.detect("https://youtube.com.evil.cn/watch"))

check("从 /video/ 取 id", fetch.aweme_id_of("https://www.douyin.com/video/7300000000000000001") == "7300000000000000001")
check("从 modal_id 取 id", fetch.aweme_id_of("https://www.douyin.com/?modal_id=7300000000000000002") == "7300000000000000002")
check("短链取不到 id", fetch.aweme_id_of("https://v.douyin.com/abc/") is None)

check("Safari 提示含 safaridriver", "safaridriver" in browser.safari_hint())
check("Safari 提示含「允许远程自动化」", "允许远程自动化" in browser.safari_hint())

tmp = os.path.join(HERE, "_tmp_fetch")
os.makedirs(tmp, exist_ok=True)
out = os.path.join(tmp, "out")

# dry-run 不许联网（L-14）：起一个「先打补丁再跑 CLI」的子进程，把
# fetch.resolve_redirect 换成恒等函数。这样 argv / 退出码 / stdout 仍然是真路径。
_runner = os.path.join(tmp, "offline_cli.py")
with open(_runner, "w", encoding="utf-8") as fh:
    fh.write("import sys\n"
             "sys.path.insert(0, %r)\n"
             "from vsum import fetch\n"
             "fetch.resolve_redirect = lambda url, timeout=15: url\n"
             "from vsum import cli\n"
             "sys.exit(cli.main(sys.argv[1:]))\n" % ROOT)


def run_offline(*argv):
    return subprocess.run([sys.executable, _runner] + list(argv), cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


try:
    r = run_offline("fetch", "https://v.douyin.com/abc123/", "-o", out, "--dry-run")
    check("dry-run 退出 0", r.returncode == 0, (r.returncode, r.stderr))
    check("dry-run 打印计划", "计划" in r.stdout, r.stdout)
    check("dry-run 不建目录", not os.path.isdir(out), out)

    r2 = run("fetch", "https://example.com/nope", "-o", out)
    check("不认识的链接退出 4", r2.returncode == 4, (r2.returncode, r2.stderr))
    check("错误走 stderr 且无 traceback", "Traceback" not in r2.stderr and r2.stderr.startswith("error:"), r2.stderr)

    r3 = run_offline("fetch", "https://v.douyin.com/abc/", "-o", out, "--dry-run", "--browser", "chrome")
    check("能强制指定浏览器", r3.returncode == 0 and "chrome" in r3.stdout, (r3.returncode, r3.stdout, r3.stderr))

    r4 = run_offline("fetch", "https://v.douyin.com/abc/", "-o", out, "--dry-run", "--browser", "edge", "--headless", "--profile", "/tmp/vsum-p")
    check("edge + headless + profile 参数可达", r4.returncode == 0 and "edge" in r4.stdout, (r4.returncode, r4.stdout, r4.stderr))
    check("dry-run 不建 profile 目录", not os.path.isdir("/tmp/vsum-p"), "")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# dry-run 在本进程里也不碰网络/浏览器：_http_get 一被调用就炸，dry_run 仍应正常返回
_get_dry = fetch._http_get
_redirect_dry = fetch.resolve_redirect
_open_dry = browser.open_browser


def _boom(*a, **kw):
    raise AssertionError("dry-run 不该发起网络请求")


try:
    fetch._http_get = _boom
    fetch.resolve_redirect = lambda url, timeout=15: url
    browser.open_browser = _boom
    plan = fetch.douyin("https://v.douyin.com/abc123/", os.path.join(tmp, "x"), dry_run=True)
    check("dry-run 不联网就出计划", plan.get("dry_run") is True, plan)
    check("dry-run 计划带浏览器名", plan.get("browser") == "auto", plan)
finally:
    fetch._http_get = _get_dry
    fetch.resolve_redirect = _redirect_dry
    browser.open_browser = _open_dry

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
    yt_url = "https://www.bilibili.com/video/BV1xx"
    fetch.run(yt_url, tmp_yt)
    pj = json.load(open(os.path.join(tmp_yt, "play.json"), encoding="utf-8"))
    check("yt-dlp 写 play.json", pj.get("url", "").endswith("BV1xx"), pj)
    check("play.json 记 videoBytes", pj.get("videoBytes") == 32, pj)
    check("play.json 含 probeSec 键", "probeSec" in pj, pj)
    check("play.json 记 source_url", pj.get("source_url") == yt_url, pj)
finally:
    fetch.ytdlp = _orig
    shutil.rmtree(tmp_yt, ignore_errors=True)


# 原子下载：先写 .part，校验通过才替换；失败只删 .part，上一次的好视频不能被抹掉
class _FakeResp:
    """够 urllib 用的假响应：read(n)、headers、上下文管理。"""

    def __init__(self, body=b"", content_type=None, fail_after=None):
        self._body = body
        self._pos = 0
        self._fail_after = fail_after
        self.headers = {} if content_type is None else {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if self._fail_after is not None and self._pos >= self._fail_after:
            raise OSError("HTTP Error 403: Forbidden")
        size = 64 if (n is None or n < 0) else n
        chunk = self._body[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


_GOOD_BODY = b"\x00\x00\x00\x18ftypmp42" + b"v" * (fetch.MIN_BYTES + 10)
_OLD = b"OLD-GOOD-VIDEO" * 4

dl_dir = tempfile.mkdtemp(prefix="vsum-fetch-dl-")
_get = fetch._http_get
_hits = fetch._resolve_hits
_DL_URL = "https://v3-web.douyinvod.com/aweme/v1/play/x"
try:
    # 下载闸门会解析域名：测试里给一个假的公网地址，不真的查 DNS
    fetch._resolve_hits = lambda host: [(2, 1, 6, "", ("93.184.216.34", 0))]
    good = os.path.join(dl_dir, "video.mp4")
    with open(good, "wb") as fh:
        fh.write(_OLD)

    # 1) 下到一半 403：retry 一次也是 403 -> FetchError，旧文件必须原样在
    fetch._http_get = lambda *a, **kw: _FakeResp(
        body=b"\x00" * (fetch.MIN_BYTES * 2), fail_after=fetch.MIN_BYTES // 2)
    try:
        fetch._download(_DL_URL, good, attempts=1)
        check("半截下载要抛 FetchError", False)
    except fetch.FetchError as exc:
        check("半截下载要抛 FetchError", "直链下载失败" in str(exc), str(exc))
    check("失败后旧视频原样保留", read_bytes(good) == _OLD, read_bytes(good)[:40])
    check("失败不留 .part", not os.path.exists(good + ".part"))
    check("失败不留下半截 video.mp4", os.path.getsize(good) == len(_OLD))

    # 2) 风控 200 + HTML：Content-Type 或者正文第一个字符就能认出来
    fetch._http_get = lambda *a, **kw: _FakeResp(
        body=b"<!DOCTYPE html><html>risk control</html>", content_type="text/html; charset=utf-8")
    try:
        fetch._download(_DL_URL, good, attempts=1)
        check("HTML 页面当视频要报错", False)
    except fetch.FetchError as exc:
        check("HTML 页面当视频要报错", "HTML" in str(exc), str(exc))
    check("HTML 失败后旧视频原样保留", read_bytes(good) == _OLD)
    check("HTML 失败不留 .part", not os.path.exists(good + ".part"))

    # 3) 没有 Content-Type，但正文以 '<' 开头（有的错误页就这样）
    fetch._http_get = lambda *a, **kw: _FakeResp(body=b"  <html>blocked</html>")
    try:
        fetch._download(_DL_URL, good, attempts=1)
        check("正文以 < 开头也要拒", False)
    except fetch.FetchError as exc:
        check("正文以 < 开头也要拒", "HTML" in str(exc), str(exc))

    # 4) 正常下载：原子替换成新内容，不留 .part
    fetch._http_get = lambda *a, **kw: _FakeResp(body=_GOOD_BODY, content_type="video/mp4")
    size = fetch._download(_DL_URL, good, attempts=1)
    check("下载成功返回字节数", size == len(_GOOD_BODY), size)
    check("成功时原子替换成新视频", read_bytes(good) == _GOOD_BODY)
    check("成功不留 .part", not os.path.exists(good + ".part"))

    # 5) 下载闸门放在重试循环之前：地址不合规立刻拒绝，不用先试两次
    fetch._http_get = lambda *a, **kw: _FakeResp(body=_GOOD_BODY)
    try:
        fetch._download("http://v3-web.douyinvod.com/x", good, attempts=2)
        check("闸门拒绝不会走重试", False)
    except fetch.FetchError as exc:
        check("闸门拒绝不会走重试", "只允许 https" in str(exc), str(exc))
        check("闸门拒绝不动旧文件", read_bytes(good) == _GOOD_BODY)
finally:
    fetch._http_get = _get
    fetch._resolve_hits = _hits
    shutil.rmtree(dl_dir, ignore_errors=True)


# 下载地址白名单（M-6）：play_addr 是接口返回的，不能当可信输入
def _url_rejected(url):
    try:
        fetch._check_url(url)
        return False
    except fetch.FetchError:
        return True


_hits2 = fetch._resolve_hits
try:
    fetch._resolve_hits = lambda host: [(2, 1, 6, "", ("93.184.216.34", 0))]
    check("https + 抖音 CDN 域名放行",
          fetch._check_url("https://v3-web.douyinvod.com/aweme/v1/play/x") is None)
    check("子域放行（v1.ytimg.com）", fetch._check_url("https://v1.ytimg.com/x") is None)
    check("http:// 非回环被拒", _url_rejected("http://v3-web.douyinvod.com/x"))
    check("file:// 被拒", _url_rejected("file:///etc/passwd"))
    check("ftp:// 被拒", _url_rejected("ftp://v1.ytimg.com/x"))
    check("不在白名单的域名被拒", _url_rejected("https://evil.example/x"))
    check("长得像白名单的域名被拒", _url_rejected("https://douyinvod.com.evil.cn/x"))
    check("白名单域名的父域被拒", _url_rejected("https://vod.douyinvod.cn/x"))

    fetch._resolve_hits = lambda host: [(2, 1, 6, "", ("127.0.0.1", 0))]
    check("解析到回环地址被拒", _url_rejected("https://v3-web.douyinvod.com/x"))
    fetch._resolve_hits = lambda host: [(2, 1, 6, "", ("10.1.2.3", 0))]
    check("解析到内网 IP 被拒", _url_rejected("https://v3-web.douyinvod.com/x"))
    fetch._resolve_hits = lambda host: [(2, 1, 6, "", ("192.168.1.5", 0))]
    check("解析到 192.168 被拒", _url_rejected("https://v3-web.douyinvod.com/x"))
    fetch._resolve_hits = lambda host: [(2, 1, 6, "", ("169.254.169.254", 0))]
    check("解析到链路本地/元数据地址被拒", _url_rejected("https://v3-web.douyinvod.com/x"))
    fetch._resolve_hits = lambda host: [(10, 1, 6, "", ("::1", 0, 0, 0))]
    check("解析到 IPv6 回环被拒", _url_rejected("https://v3-web.douyinvod.com/x"))
    fetch._resolve_hits = lambda host: []
    check("解析不出地址要报 FetchError", _url_rejected("https://v3-web.douyinvod.com/x"))

    def _dns_fail(host):
        raise OSError("Name or service not known")

    fetch._resolve_hits = _dns_fail
    check("DNS 失败要报 FetchError", _url_rejected("https://v3-web.douyinvod.com/x"))
finally:
    fetch._resolve_hits = _hits2


# 重定向：同 host:port 才跟随，跨域名一律拒绝
_redirect = fetch._SameHostRedirect()
_req = urllib.request.Request("https://v3-web.douyinvod.com/aweme/v1/play/x")
try:
    _redirect.redirect_request(_req, None, 302, "Found", {},
                               "https://v3-web.douyinvod.com/aweme/v1/play/y")
    check("同域名重定向放行", True)
except fetch.FetchError as exc:
    check("同域名重定向放行", False, str(exc))
try:
    _redirect.redirect_request(_req, None, 302, "Found", {}, "https://evil.example/x")
    check("跨域名重定向被拒", False)
except fetch.FetchError as exc:
    check("跨域名重定向被拒", "跨域名重定向" in str(exc), str(exc))
try:
    _redirect.redirect_request(_req, None, 302, "Found", {}, "http://v3-web.douyinvod.com/x")
    check("重定向到 http 被拒", False)
except fetch.FetchError as exc:
    check("重定向到 http 被拒", "跨域名重定向" in str(exc), str(exc))
try:
    _redirect.redirect_request(_req, None, 302, "Found", {},
                               "https://v3-web.douyinvod.com:8443/x")
    check("同域名换端口也被拒", False)
except fetch.FetchError as exc:
    check("同域名换端口也被拒", "跨域名重定向" in str(exc), str(exc))


# yt-dlp 的 ydl_opts：不能顺着播放列表下，outtmpl 里的 % 要转义
pct_root = tempfile.mkdtemp(prefix="vsum-fetch-pct-")
pct_dir = os.path.join(pct_root, "out 100%")
os.makedirs(pct_dir, exist_ok=True)
_seen = {}


class _FakeYDL:
    def __init__(self, opts):
        _seen.clear()
        _seen.update(opts)
        self._path = os.path.join(pct_dir, "video.mp4")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        with open(self._path, "wb") as fh:
            fh.write(b"v" * (fetch.MIN_BYTES + 1))
        return {"title": "t", "duration": 3.0, "ext": "mp4", "filepath": self._path}

    def prepare_filename(self, info):
        return info["filepath"]


_old_mod = sys.modules.get("yt_dlp")
try:
    sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=_FakeYDL)
    info = fetch.ytdlp("https://www.youtube.com/watch?v=abcdefghijk", pct_dir)
    check("yt-dlp 路径返回视频", info.get("video") == os.path.join(pct_dir, "video.mp4"), info)
    check("yt-dlp 关掉播放列表", _seen.get("noplaylist") is True, _seen.get("noplaylist"))
    check("outtmpl 只转义目录里的 %",
          _seen.get("outtmpl") == os.path.join(pct_dir.replace("%", "%%"), "video.%(ext)s"),
          _seen.get("outtmpl"))
    check("outtmpl 的 %(ext)s 没被转义坏", _seen.get("outtmpl").endswith("video.%(ext)s"),
          _seen.get("outtmpl"))
finally:
    if _old_mod is None:
        sys.modules.pop("yt_dlp", None)
    else:
        sys.modules["yt_dlp"] = _old_mod
    shutil.rmtree(pct_root, ignore_errors=True)


# 复用：play.json 记的是同一个链接 + 视频文件还在且不像半截 -> 不下载、不起浏览器
reuse_dir = tempfile.mkdtemp(prefix="vsum-fetch-reuse-")
_orig_ytdlp = fetch.ytdlp
try:
    reuse_url = "https://www.bilibili.com/video/BVreuse"
    reuse_video = os.path.join(reuse_dir, "video.mp4")
    with open(reuse_video, "wb") as fh:
        fh.write(b"v" * (fetch.MIN_BYTES + 1))
    with open(os.path.join(reuse_dir, "play.json"), "w", encoding="utf-8") as fh:
        json.dump({"ok": True, "kind": "bilibili", "source_url": reuse_url, "video": reuse_video,
                   "warnings": [], "durationSec": 3.0}, fh, ensure_ascii=False)

    calls = []
    fetch.ytdlp = lambda *a, **kw: calls.append(1) or {"video": "/nope"}
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            reused = fetch.run(reuse_url, reuse_dir)
        check("复用命中时跳过下载", calls == [], calls)
        check("复用结果带 video", reused.get("video") == reuse_video, reused)
        check("复用结果标了 reused", reused.get("reused") is True, reused)
        check("复用结果带 warnings", isinstance(reused.get("warnings"), list), reused)
        check("复用打印提示", "复用" in buf.getvalue(), buf.getvalue())
        check("复用刷新 videoBytes", reused.get("videoBytes") == os.path.getsize(reuse_video),
              reused.get("videoBytes"))

        with redirect_stdout(io.StringIO()):
            fetch.run(reuse_url, reuse_dir, force=True)
        check("force=True 会重新下载", calls == [1], calls)

        calls.clear()
        with redirect_stdout(io.StringIO()):
            fetch.run("https://www.bilibili.com/video/BVother", reuse_dir)
        check("换了链接就不复用", calls == [1], calls)
        check("链接不符不复用", fetch._reusable(reuse_dir, "https://b23.tv/other") is None)
    finally:
        fetch.ytdlp = _orig_ytdlp
finally:
    fetch.ytdlp = _orig_ytdlp
    shutil.rmtree(reuse_dir, ignore_errors=True)

# 半截文件（小到不可能）不算可复用
small_dir = tempfile.mkdtemp(prefix="vsum-fetch-small-")
try:
    small_video = os.path.join(small_dir, "video.mp4")
    with open(small_video, "wb") as fh:
        fh.write(b"x")
    with open(os.path.join(small_dir, "play.json"), "w", encoding="utf-8") as fh:
        json.dump({"source_url": "https://b23.tv/tiny", "video": small_video}, fh)
    check("太小的视频不复用", fetch._reusable(small_dir, "https://b23.tv/tiny") is None)
    check("play.json 都没有时不复用", fetch._reusable(os.path.join(small_dir, "nope"), "x") is None)
finally:
    shutil.rmtree(small_dir, ignore_errors=True)


# play.json 是外部数据：指向 out_dir 外面的路径不能当「上次下好的视频」用（M-6）
evil_root = tempfile.mkdtemp(prefix="vsum-fetch-evil-")
try:
    evil_out = os.path.join(evil_root, "out")
    os.makedirs(evil_out)
    outside = os.path.join(evil_root, "id_rsa")
    with open(outside, "wb") as fh:
        fh.write(b"k" * (fetch.MIN_BYTES + 1))
    with open(os.path.join(evil_out, "play.json"), "w", encoding="utf-8") as fh:
        json.dump({"source_url": "https://b23.tv/evil", "video": outside}, fh)
    check("play.json 指向 out_dir 外不复用",
          fetch._reusable(evil_out, "https://b23.tv/evil") is None)

    # 相对路径也要落在 out_dir 里，别跟着 cwd 走
    with open(os.path.join(evil_out, "play.json"), "w", encoding="utf-8") as fh:
        json.dump({"source_url": "https://b23.tv/evil", "video": "../../id_rsa"}, fh)
    check("相对路径跳出 out_dir 不复用",
          fetch._reusable(evil_out, "https://b23.tv/evil") is None)

    # out_dir 里的软链接指到外面：realpath 之后就出去了，同样不认
    link = os.path.join(evil_out, "video.mp4")
    try:
        os.symlink(outside, link)
        with open(os.path.join(evil_out, "play.json"), "w", encoding="utf-8") as fh:
            json.dump({"source_url": "https://b23.tv/evil", "video": link}, fh)
        check("out_dir 内指向外部的软链接不复用",
              fetch._reusable(evil_out, "https://b23.tv/evil") is None)
    except (OSError, NotImplementedError):   # Windows 无权限建软链接就跳过
        pass

    inside = os.path.join(evil_out, "good.mp4")
    with open(inside, "wb") as fh:
        fh.write(b"v" * (fetch.MIN_BYTES + 1))
    with open(os.path.join(evil_out, "play.json"), "w", encoding="utf-8") as fh:
        json.dump({"source_url": "https://b23.tv/evil", "video": inside}, fh)
    check("out_dir 内的视频照常复用",
          (fetch._reusable(evil_out, "https://b23.tv/evil") or {}).get("video") == inside)
finally:
    shutil.rmtree(evil_root, ignore_errors=True)

# 不认识的链接：先认类型再建目录，别在磁盘上留一个空目录
leftover = os.path.join(tempfile.mkdtemp(prefix="vs-fetch-"), "should-not-exist")
try:
    fetch.run("https://example.com/not-a-video", leftover)
except fetch.FetchError:
    check("不认识的链接抛 FetchError", True)
else:
    check("不认识的链接抛 FetchError", False)
check("不认识的链接不留空目录", not os.path.exists(leftover), leftover)

if FAIL:
    for f in FAIL:
        print("FAIL " + f)
    sys.exit(1)
print("OK fetch (%d checks)" % N[0])

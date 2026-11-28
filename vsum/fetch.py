# -*- coding: utf-8 -*-
"""链接抓取：抖音走浏览器页面里的官方 detail 接口，B站/YouTube 走 yt-dlp。

抖音为什么要绕页面：
- yt-dlp 对抖音早已失效（403 / Fresh cookies needed）；
- 官方 detail 接口带签名校验，直接用 requests 打会 403；
- 在浏览器上下文里 `fetch(..., {credentials:'include'})`，签名由页面自己的 JS 附带。
"""
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request

from .toolpath import which

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 域名表：只认「host 等于域名或是它的子域」，别用子串匹配（notyoutube.com 不是 YouTube）
HOSTS = (
    ("douyin", ("douyin.com", "iesdouyin.com")),
    ("bilibili", ("bilibili.com", "b23.tv")),
    ("youtube", ("youtube.com", "youtu.be")),
)

# 直链下载只允许这些域名（含子域）。play_addr 来自接口返回体，不能当可信输入：
# 否则一个被篡改/风控污染的响应就能让 vsum 去下载任意 URL（内网探测、file:// 读本地文件）。
DOWNLOAD_HOSTS = ("douyin.com", "douyinvod.com", "bytecdn.cn", "bilivideo.com",
                  "bilibili.com", "akamaized.net", "googlevideo.com", "youtube.com",
                  "ytimg.com")

# 小到这个程度的「视频」肯定是错误页/半截文件
MIN_BYTES = 64 * 1024

# 页面内调 detail 接口时要带的参数（从线上请求抄的，缺一个就可能被风控）
DETAIL_JS = """
const cb = arguments[arguments.length - 1];
(async () => {
  const p = new URLSearchParams({
    device_platform: 'webapp', aid: '6383', channel: 'channel_pc_web',
    aweme_id: String(arguments[0]),
    pc_client_type: '1', version_code: '170400', version_name: '17.4.0',
    cookie_enabled: 'true', screen_width: '1920', screen_height: '1080',
    browser_language: 'zh-CN', browser_platform: 'MacIntel', browser_name: 'Chrome',
    browser_version: '124.0.0.0', browser_online: 'true', engine_name: 'Blink',
    engine_version: '124.0.0.0', os_name: 'Mac OS', os_version: '10.15.7',
    cpu_core_num: '8', device_memory: '8', platform: 'PC',
    downlink: '10', effective_type: '4g', round_trip_time: '50'
  });
  const r = await fetch('/aweme/v1/web/aweme/detail/?' + p.toString(), { credentials: 'include' });
  const t = await r.text();
  cb(JSON.stringify({ status: r.status, body: t }));
})().catch(e => cb(JSON.stringify({ status: 0, error: String(e) })));
"""


class FetchError(RuntimeError):
    """抓取失败（URL 不认识、风控、直链过期）。CLI 映射成退出码 4。"""


def _host_of(url):
    """取 host（小写、去掉端口和末尾的点）。没写 scheme 的裸字符串也尽量认。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "//" + raw
    try:
        host = urllib.parse.urlparse(raw).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def detect(url):
    host = _host_of(url)
    if not host:
        return None
    for kind, domains in HOSTS:
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return kind
    return None


def _host_allowed(host):
    host = (host or "").lower().rstrip(".")
    for domain in DOWNLOAD_HOSTS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _resolve_hits(host):
    """DNS 解析。单独包一层是为了测试能 monkeypatch（不真解析、不联网）。"""
    return socket.getaddrinfo(host, None)


def _check_url(url):
    """下载地址闸门：只允许 https + 白名单域名 + 解析结果不是内网/保留地址。

    play_addr 是接口返回体里的字段（页面上下文取得，可能被风控页/篡改影响），
    urlopen 本身还会接受 file:// 和任意重定向，所以在这里一次性挡掉。
    """
    raw = (url or "").strip()
    try:
        parsed = urllib.parse.urlparse(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise FetchError("看不懂的下载地址：%s（%s）" % (raw[:120], exc))
    if parsed.scheme.lower() != "https":
        raise FetchError("拒绝下载这个地址（只允许 https）：%s" % (raw[:120] or "(空)"))
    if not host or not _host_allowed(host):
        raise FetchError("拒绝下载这个地址（不在允许的域名里）：%s" % raw[:120])
    try:
        hits = _resolve_hits(host)
    except (OSError, UnicodeError) as exc:
        raise FetchError("解析不了下载地址的域名：%s（%s）" % (host, exc))
    if not hits:
        raise FetchError("解析不了下载地址的域名：%s" % host)
    for hit in hits:
        try:
            addr = hit[4][0]
        except (IndexError, TypeError):
            raise FetchError("下载地址解析结果看不懂：%s" % (hit,))
        try:
            ip = ipaddress.ip_address(str(addr).split("%")[0])
        except ValueError:
            raise FetchError("下载地址解析出看不懂的 IP：%s" % (addr,))
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise FetchError("拒绝下载这个地址（解析到内网/保留地址 %s）：%s" % (addr, host))


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """只跟随同 host:port 的重定向。

    CDN 在自家域名之间跳（同一个 host 换路径）是正常的；跳到别的域名一律拒绝，
    否则白名单只挡住第一跳，后续跳转可以把人带到任意地址。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            old = urllib.parse.urlsplit(req.full_url)
            new = urllib.parse.urlsplit(newurl)
        except ValueError as exc:
            raise FetchError("重定向地址看不懂：%s（%s）" % (newurl[:120], exc))
        if (new.scheme.lower(), new.hostname, new.port) != (old.scheme.lower(), old.hostname, old.port):
            raise FetchError("拒绝跟随跨域名重定向：%s → %s" % (old.netloc, new.netloc))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _AllowedHostRedirect(urllib.request.HTTPRedirectHandler):
    """页面跳转用的宽松版：允许跳到白名单内的另一个域名。

    抖音短链就是 v.douyin.com → www.douyin.com/video/…，跨了 host 但仍是自家域名；
    下载直链用上面那个严格版（CDN 不该带着 Referer/授权乱跳）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(
    urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(), _SameHostRedirect())
_PAGE_OPENER = urllib.request.build_opener(
    urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(), _AllowedHostRedirect())


def _http_get(url, referer=None, timeout=20):
    _check_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": referer or url,
    })
    return _OPENER.open(req, timeout=timeout)


def _inside(path, root):
    """path 的真实路径是否落在 root 里面（含等于 root）。用于挡住 play.json 里伪造的路径。"""
    try:
        real = os.path.realpath(path)
        base = os.path.realpath(root)
    except OSError:
        return False
    if real == base:
        return True
    try:
        return os.path.commonpath([real, base]) == base
    except ValueError:   # Windows 上不同盘符没法取公共前缀
        return False


def resolve_redirect(url, timeout=15):
    """短链（v.douyin.com/xxx）跟到最终页面。失败就原样返回。"""
    try:
        _check_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": url})
        with _PAGE_OPENER.open(req, timeout=timeout) as r:
            return r.geturl() or url
    except Exception:
        return url


def aweme_id_of(url):
    m = re.search(r"/(?:video|note)/(\d{8,})", url)
    if m:
        return m.group(1)
    m = re.search(r"modal_id=(\d{8,})", url)
    return m.group(1) if m else None


def _pick_play_url(detail):
    """挑码率最高的直链，但只在允许的域名里挑：接口给什么都不能照单全收。"""
    video = detail.get("video") or {}
    rates = video.get("bit_rate") or []
    best = None
    for r in rates:
        addr = (r.get("play_addr") or {}).get("url_list") or []
        score = r.get("bit_rate") or 0
        for u in addr:
            if not _host_allowed(_host_of(u)):
                continue
            if best is None or score > best[0]:
                best = (score, u)
            break
    if best:
        return best[1]
    addr = (video.get("play_addr") or {}).get("url_list") or []
    for u in addr:
        if _host_allowed(_host_of(u)):
            return u
    return None


def _looks_like_html(head, content_type=""):
    """风控/错误页常常是 200 + HTML：Content-Type 说是 html，或正文以 '<' 开头。"""
    if content_type and "html" in content_type.lower():
        return True
    return head.lstrip()[:1] == b"<"


def _check_downloaded(total, head, content_type=""):
    """落盘前校验：空的 / 太小 / 是 HTML 页面的都不算视频。"""
    if total <= 0:
        raise FetchError("下载结果是空文件")
    if _looks_like_html(head, content_type):
        raise FetchError("下载结果是 HTML 页面（多半撞上风控了），不是视频")
    if total < MIN_BYTES:
        raise FetchError("下载结果只有 %d 字节，不像视频" % total)


def _download(url, path, referer="https://www.douyin.com/", attempts=2):
    """直链下载：先写 path.part，校验通过再原子替换。

    之前是直接 open(path, "wb") + 失败删除，一次 403/直链过期就会把上一次
    已经下好的 video.mp4 抹掉。现在失败只删 .part，旧文件一动不动。
    """
    tmp = path + ".part"
    _check_url(url)
    last = None
    for attempt in range(1, attempts + 1):
        total = 0
        head = b""
        content_type = ""
        try:
            with _http_get(url, referer=referer, timeout=60) as r:
                headers = getattr(r, "headers", None)
                if headers is not None:
                    content_type = headers.get("Content-Type") or ""
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        if len(head) < 64:
                            head += chunk[:64 - len(head)]
                        fh.write(chunk)
                        total += len(chunk)
                        if total % (1 << 22) < (1 << 16):
                            print("  已下载 %.1f MB" % (total / 1048576.0), flush=True)
            _check_downloaded(total, head, content_type)
            os.replace(tmp, path)
            return total
        except Exception as exc:
            last = exc
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if attempt < attempts:
                print("  下载失败（%s），1 秒后重试一次" % exc, flush=True)
                time.sleep(1)
    raise FetchError("直链下载失败：%s（play_addr 可能已过期，重跑一次即可；已有的视频文件未动）" % last)


def _probe_duration(path):
    from . import pipeline

    try:
        return pipeline.probe_duration(path)
    except Exception:
        return None


def _reusable(out_dir, url):
    """这个输出目录里已经有同一个链接下好的视频 → 返回 play.json 内容，否则 None。

    判据要三条都成立，缺一条就老老实实重下：play.json 记的是这个 URL、
    它指的视频文件还在、而且不是半截文件。
    """
    play_path = os.path.join(out_dir, "play.json")
    if not os.path.exists(play_path):
        return None
    try:
        with open(play_path, encoding="utf-8") as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(prev, dict):
        return None
    if (prev.get("source_url") or "").strip() != (url or "").strip():
        return None
    video = prev.get("video")
    if not video:
        return None
    if not os.path.isabs(video):
        video = os.path.join(out_dir, video)
    # play.json 可能是别人给的目录里的文件：指向 out_dir 外面的路径一律不认，
    # 否则 .ssh/id_rsa 只要大于 64KB 就会被当成「上次下好的视频」送进 ffmpeg
    if not _inside(video, out_dir) or not os.path.exists(video):
        return None
    video = os.path.abspath(video)
    try:
        if os.path.getsize(video) < MIN_BYTES:
            return None
    except OSError:
        return None
    prev["video"] = video
    return prev


def _reuse_result(prev, url, out_dir):
    """复用上一次的下载结果：刷新大小/时长（都很便宜），重写 play.json。"""
    video = prev["video"]
    prev["videoBytes"] = os.path.getsize(video)
    probed = _probe_duration(video)
    if probed is not None:
        prev["probeSec"] = probed
    prev["source_url"] = url
    prev["outDir"] = out_dir
    prev["reused"] = True
    prev.setdefault("warnings", [])
    _write_play_json(out_dir, prev)
    print("复用上次的视频：%s（%.1f MB，跳过浏览器和下载；要重下加 --force）"
          % (video, prev["videoBytes"] / 1048576.0), flush=True)
    return prev


def _write_play_json(out_dir, result):
    with open(os.path.join(out_dir, "play.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)


def _fetch_detail(driver, url, final_url, aid):
    """在页面上下文里取 detail 原始 JSON，返回 (raw, page_url, aid)。"""
    driver.set_page_load_timeout(40)
    driver.get(url if url != final_url else final_url)
    for _ in range(40):
        if driver.execute_script("return document.readyState") == "complete":
            break
        time.sleep(0.5)
    page_url = driver.execute_script("return location.href") or final_url
    aid = aweme_id_of(page_url) or aid
    raw = driver.execute_async_script(DETAIL_JS, aid)
    return raw, page_url, aid


def douyin(url, out_dir, browser=None, dry_run=False, headless=None, profile=None, force=False):
    """返回 dict（写进 play.json）。dry_run 时只打印计划，不起浏览器。

    force=False 且 out_dir 里已经有这个链接下好的视频时，连浏览器都不起。
    """
    from . import browser as browser_mod

    if dry_run:
        final_url = resolve_redirect(url)
        aid = aweme_id_of(final_url)
        return {"ok": True, "dry_run": True, "kind": "douyin", "url": final_url, "awemeId": aid,
                "browser": browser or "auto",
                "note": "默认起可见窗口过风控；--headless 是可选加速"}
    if not force:
        prev = _reusable(out_dir, url)
        if prev:
            return _reuse_result(prev, url, out_dir)

    final_url = resolve_redirect(url)
    aid = aweme_id_of(final_url)
    if not aid:
        raise FetchError("从 URL 里没认出视频 id：%s（短链可能没跳转成功，换成 www.douyin.com/video/<id>）" % final_url)

    driver, kind = browser_mod.open_browser(prefer=browser, headless=headless, profile=profile)
    print("浏览器：%s（profile %s）" % (kind, browser_mod.profile_dir(profile)), flush=True)
    try:
        last = None
        raw = page_url = None
        for attempt in (1, 2):
            try:
                raw, page_url, aid = _fetch_detail(driver, url, final_url, aid)
                break
            except Exception as exc:
                last = exc
                if attempt == 1:
                    print("  取 detail 失败（%s），1 秒后重试一次" % exc, flush=True)
                    time.sleep(1)
        if raw is None:
            raise FetchError("取 detail 失败（重试一次仍失败）：%s" % last)
    finally:
        browser_mod.close(driver)

    try:
        payload = json.loads(raw)
    except ValueError:
        raise FetchError("detail 接口返回的不是 JSON：%s" % str(raw)[:200])
    if payload.get("status") != 200:
        raise FetchError("detail 接口 HTTP %s：%s" % (payload.get("status"), str(payload.get("body"))[:200]))
    try:
        body = json.loads(payload["body"])
    except ValueError:
        raise FetchError("detail 接口 body 不是 JSON（多半是风控页）")
    detail = body.get("aweme_detail")
    if not detail:
        raise FetchError("detail 接口没给 aweme_detail（风控或视频已删）")

    play = _pick_play_url(detail)
    if not play:
        raise FetchError("没有可用的 play_addr（可能是图文帖，或返回的地址不在允许的域名里）")
    duration = round((detail.get("video") or {}).get("duration", 0) / 1000.0, 1)
    return {
        "ok": True, "kind": "douyin", "url": page_url, "awemeId": aid,
        "author": (detail.get("author") or {}).get("nickname"),
        "desc": detail.get("desc"),
        "durationSec": duration,
        "playAddr": play,
        "outDir": out_dir,
        "warnings": [],
    }


def _outtmpl(out_dir):
    """yt-dlp 的 outtmpl 会展开 %-序列：目录里的 % 必须写成 %%，只转义目录部分。"""
    return os.path.join(out_dir.replace("%", "%%"), "video.%(ext)s")


def ytdlp(url, out_dir, dry_run=False):
    if dry_run:
        have = which("yt-dlp")
        try:
            import yt_dlp  # noqa: F401
            have = have or "python:yt_dlp"
        except ImportError:
            pass
        return {"ok": True, "dry_run": True, "kind": detect(url) or "other", "url": url,
                "tool": have or "缺失：pip install yt-dlp"}
    try:
        import yt_dlp
    except ImportError as exc:
        raise FetchError("没有 yt-dlp：pip install yt-dlp") from exc
    outtmpl = _outtmpl(out_dir)
    opts = {
        "outtmpl": outtmpl,
        "quiet": False,
        "noprogress": False,
        "retries": 3,
        # 带播放列表的链接只下当前这一个视频，别顺着列表把整个合集拉下来
        "noplaylist": True,
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        raise FetchError("yt-dlp 抓取失败：%s" % exc) from exc
    path = ydl.prepare_filename(info)
    if not os.path.exists(path):
        # prepare_filename 给的是合并前的扩展名（webm/mkv），merge 后是 .mp4
        alt = os.path.splitext(path)[0] + ".mp4"
        if os.path.exists(alt):
            path = alt
    return {"ok": True, "kind": detect(url) or "other", "url": url,
            "title": info.get("title"), "durationSec": info.get("duration"),
            "video": path}


def run(url, out_dir, browser=None, dry_run=False, headless=None, profile=None, force=False):
    """抓取入口。force=False 时同一个链接重跑会复用 out_dir 里已有的视频。"""
    kind = detect(url)
    if kind is None:
        # 先认链接再建目录：不认识的链接不该在磁盘上留一个空目录
        raise FetchError("不认识的链接：%s（支持抖音 / B站 / YouTube）" % url)
    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)

    if not dry_run and not force:
        prev = _reusable(out_dir, url)
        if prev:
            return _reuse_result(prev, url, out_dir)

    if kind == "douyin":
        result = douyin(url, out_dir, browser=browser, dry_run=dry_run,
                        headless=headless, profile=profile, force=force)
        if dry_run:
            print("计划：%s → %s（浏览器 %s）" % (url, out_dir, result["browser"]))
            print("注意：默认弹可见窗口；headless 可能被风控，失败就去掉 --headless")
            return result
        video = os.path.join(out_dir, "video.mp4")
        print("下载视频（play_addr 是时效直链，失败自动重试一次）", flush=True)
        size = _download(result["playAddr"], video)
        probed = _probe_duration(video)
        result["video"] = video
        result["videoBytes"] = size
        result["probeSec"] = probed
        result["source_url"] = url
        if probed and result["durationSec"] and abs(probed - result["durationSec"]) > 1.0:
            result["warnings"].append("时长不一致：接口 %.1fs vs 文件 %.1fs（文件已落盘，继续用）"
                                      % (result["durationSec"], probed))
        _write_play_json(out_dir, result)
        print("完成：%s（%.1f MB，%.1fs）" % (video, size / 1048576.0, probed or 0))
        return result

    result = ytdlp(url, out_dir, dry_run=dry_run)
    if not dry_run:
        video = result.get("video")
        if video and os.path.exists(video):
            result["videoBytes"] = os.path.getsize(video)
            result["probeSec"] = _probe_duration(video)
        result["source_url"] = url
        result.setdefault("warnings", [])
        _write_play_json(out_dir, result)
        print("完成：%s" % result.get("video", out_dir))
    return result

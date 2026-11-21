# -*- coding: utf-8 -*-
"""链接抓取：抖音走浏览器页面里的官方 detail 接口，B站/YouTube 走 yt-dlp。

抖音为什么要绕页面：
- yt-dlp 对抖音早已失效（403 / Fresh cookies needed）；
- 官方 detail 接口带签名校验，直接用 requests 打会 403；
- 在浏览器上下文里 `fetch(..., {credentials:'include'})`，签名由页面自己的 JS 附带。
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

DOUYIN = re.compile(r"(douyin\.com|iesdouyin\.com)")
BILI = re.compile(r"(bilibili\.com|b23\.tv)")
YT = re.compile(r"(youtube\.com|youtu\.be)")

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


def detect(url):
    if DOUYIN.search(url):
        return "douyin"
    if BILI.search(url):
        return "bilibili"
    if YT.search(url):
        return "youtube"
    return None


def _http_get(url, referer=None, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": referer or url,
    })
    return urllib.request.urlopen(req, timeout=timeout)


def resolve_redirect(url, timeout=15):
    """短链（v.douyin.com/xxx）跟到最终页面。失败就原样返回。"""
    try:
        with _http_get(url, timeout=timeout) as r:
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
    video = detail.get("video") or {}
    rates = video.get("bit_rate") or []
    best = None
    for r in rates:
        addr = (r.get("play_addr") or {}).get("url_list") or []
        if not addr:
            continue
        score = r.get("bit_rate") or 0
        if best is None or score > best[0]:
            best = (score, addr[0])
    if best:
        return best[1]
    addr = (video.get("play_addr") or {}).get("url_list") or []
    return addr[0] if addr else None


def _download(url, path, referer="https://www.douyin.com/", attempts=2):
    """直链下载。play_addr 有时效，失败一次就删半截文件重试一次。"""
    last = None
    for attempt in range(1, attempts + 1):
        total = 0
        try:
            with _http_get(url, referer=referer, timeout=60) as r, open(path, "wb") as fh:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
                    if total % (1 << 22) < (1 << 16):
                        print("  已下载 %.1f MB" % (total / 1048576.0), flush=True)
            return total
        except Exception as exc:
            last = exc
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            if attempt < attempts:
                print("  下载失败（%s），1 秒后重试一次" % exc, flush=True)
                time.sleep(1)
    raise FetchError("直链下载失败（可能已过期，重跑一次即可）：%s" % last)


def _probe_duration(path):
    from . import pipeline

    try:
        return pipeline.probe_duration(path)
    except Exception:
        return None


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


def douyin(url, out_dir, browser=None, dry_run=False, headless=None, profile=None):
    """返回 dict（写进 play.json）。dry_run 时只打印计划，不起浏览器。"""
    from . import browser as browser_mod

    final_url = resolve_redirect(url)
    aid = aweme_id_of(final_url)
    if dry_run:
        return {"ok": True, "dry_run": True, "kind": "douyin", "url": final_url, "awemeId": aid,
                "browser": browser or "auto",
                "note": "默认起可见窗口过风控；--headless 是可选加速"}
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
        raise FetchError("没有可用的 play_addr（可能是图文帖）")
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


def ytdlp(url, out_dir, dry_run=False):
    if dry_run:
        have = shutil.which("yt-dlp")
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
    outtmpl = os.path.join(out_dir, "video.%(ext)s")
    opts = {
        "outtmpl": outtmpl,
        "quiet": False,
        "noprogress": False,
        "retries": 3,
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


def run(url, out_dir, browser=None, dry_run=False, headless=None, profile=None):
    kind = detect(url)
    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
    if kind is None:
        raise FetchError("不认识的链接：%s（支持抖音 / B站 / YouTube）" % url)

    if kind == "douyin":
        result = douyin(url, out_dir, browser=browser, dry_run=dry_run,
                        headless=headless, profile=profile)
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
        if probed and result["durationSec"] and abs(probed - result["durationSec"]) > 1.0:
            result["warnings"].append("时长不一致：接口 %.1fs vs 文件 %.1fs（文件已落盘，继续用）"
                                      % (result["durationSec"], probed))
        with open(os.path.join(out_dir, "play.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        print("完成：%s（%.1f MB，%.1fs）" % (video, size / 1048576.0, probed or 0))
        return result

    result = ytdlp(url, out_dir, dry_run=dry_run)
    if not dry_run:
        video = result.get("video")
        if video and os.path.exists(video):
            result["videoBytes"] = os.path.getsize(video)
            result["probeSec"] = _probe_duration(video)
        with open(os.path.join(out_dir, "play.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)
        print("完成：%s" % result.get("video", out_dir))
    return result

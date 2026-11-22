# -*- coding: utf-8 -*-
"""batch：一串链接串行跑，失败不中断，batch.json 原子落盘。

目录名 <platform>-<id>；已存在 ok 的 result.json 且没给 --force 就跳过。
kill -9 后重跑：目录里已有 segments.json + manifest 时，pipeline 的复用逻辑会跳过转写。
"""
import hashlib
import json
import os
import re
import time

from . import __version__, fetch, pipeline

BILI_BV = re.compile(r"(BV[0-9A-Za-z]{10})")
YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


def load_list(path):
    """urls.txt（一行一条，# 注释）或 index.json（{"items":[{"href"}]}）。去重保序。"""
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("items", []) if isinstance(data, dict) else data
        urls = [(it.get("href") or it.get("url") or "") if isinstance(it, dict) else str(it)
                for it in items]
    else:
        with open(path, encoding="utf-8") as fh:
            urls = [line.split("#", 1)[0].strip() for line in fh]
    seen, out = set(), []
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def slug(url):
    """<platform>-<id>；平台/ID 取不到时用 sha1(url)[:10]。"""
    kind = fetch.detect(url)
    ident = None
    if kind == "douyin":
        ident = fetch.aweme_id_of(url)
    elif kind == "bilibili":
        m = BILI_BV.search(url)
        ident = m.group(1) if m else None
    elif kind == "youtube":
        m = YT_ID.search(url)
        ident = m.group(1) if m else None
    if not kind or not ident:
        return "other-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return "%s-%s" % (kind, ident)


def _ok_result(result_path):
    try:
        with open(result_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return bool(data.get("ok"))
    except (OSError, ValueError):
        return False


def _atomic_write(data, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def run(list_path, out_dir, model="small", language="zh", pack=None,
        browser=None, headless=None, profile=None, force=False):
    urls = load_list(list_path)
    os.makedirs(out_dir, exist_ok=True)
    batch_path = os.path.join(out_dir, "batch.json")
    items = []
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    for i, url in enumerate(urls, 1):
        name = slug(url)
        target = os.path.join(out_dir, name)
        result_path = os.path.join(target, "result.json")
        print("[%d/%d] %s -> %s" % (i, len(urls), url, name))
        t0 = time.time()
        if not force and _ok_result(result_path):
            print("  跳过：已有 ok 的 result.json")
            items.append({"url": url, "dir": name, "status": "skipped"})
            counts["skipped"] += 1
            continue
        try:
            pipeline.run(url, target, model, language, pack, browser=browser,
                         headless=headless, profile=profile, force=force)
            status, error = "ok", None
        except KeyboardInterrupt:
            items.append({"url": url, "dir": name, "status": "interrupted"})
            _atomic_write({"schema": 1, "vsum_version": __version__, "out": out_dir,
                           "total": len(urls), "counts": counts, "items": items},
                          batch_path)
            raise
        except Exception as exc:
            status, error = "failed", "%s: %s" % (type(exc).__name__, exc)
            print("  失败：%s" % error)
        items.append({"url": url, "dir": name, "status": status,
                      "error": error, "elapsed_sec": round(time.time() - t0, 1)})
        counts[status] += 1
        _atomic_write({"schema": 1, "vsum_version": __version__, "out": out_dir,
                       "total": len(urls), "counts": counts, "items": items},
                      batch_path)
    data = {"schema": 1, "vsum_version": __version__, "out": out_dir,
            "total": len(urls), "counts": counts, "items": items}
    _atomic_write(data, batch_path)
    print("批量完成：ok %d，skipped %d，failed %d -> %s"
          % (counts["ok"], counts["skipped"], counts["failed"], batch_path))
    return 5 if counts["failed"] else 0
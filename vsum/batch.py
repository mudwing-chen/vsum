# -*- coding: utf-8 -*-
"""batch：一串链接串行跑，失败不中断，batch.json 原子落盘。

目录名 <platform>-<id>；已存在 ok 的 result.json 且没给 --force 就跳过。
kill -9 后重跑：目录里已有 segments.json + manifest 时，pipeline 的复用逻辑会跳过转写。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

from . import __version__, fetch, hardware, pipeline

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


def _device_list(devices):
    """--devices 0,1 / cuda:0,cuda:1 → ["cuda:0", "cuda:1"]；空/None → []。

    裸数字按 CUDA 序号理解：`--devices 0,1` 是 `nvidia-smi` 里的写法，
    直接把 "0" 塞进 WHISPER_DEVICE 在 ctranslate2 里不是合法设备名。
    """
    if not devices:
        return []
    out = []
    for part in str(devices).split(","):
        dev = part.strip()
        if not dev:
            continue
        if dev.isdigit():
            dev = "cuda:" + dev
        if dev == "cuda" or dev.startswith("cuda:") or dev == "cpu":
            out.append(dev)
        else:
            raise ValueError("--devices 只认 cpu 或 cuda[:N]：%r" % dev)
    return out


def _child_cmd(url, target, model, language, pack, browser, headless, profile,
               force, context, decode_opts):
    """并行跑时给子进程的 argv：和 CLI 的参数一一对应，不复制逻辑。"""
    cmd = [sys.executable, "-m", "vsum", url, "-o", target,
           "--model", str(model), "--lang", str(language), "--json"]
    if pack:
        cmd += ["--pack", pack]
    if not context:
        cmd += ["--no-context"]
    if force:
        cmd += ["--force"]
    if browser:
        cmd += ["--browser", browser]
    if headless:
        cmd += ["--headless"]
    if profile:
        cmd += ["--profile", profile]
    opts = decode_opts or {}
    if str(opts.get("batch_size", "1")) != "1":
        cmd += ["--batch-size", str(opts["batch_size"])]
    for flag, key in (("--vad-threshold", "threshold"),
                      ("--vad-min-speech-ms", "min_speech_duration_ms"),
                      ("--vad-min-silence-ms", "min_silence_duration_ms"),
                      ("--vad-pad-ms", "speech_pad_ms")):
        if opts.get(key) is not None:
            cmd += [flag, str(opts[key])]
    if opts.get("no_vad"):
        cmd += ["--no-vad"]
    if opts.get("chunk_length"):
        cmd += ["--chunk-length", str(opts["chunk_length"])]
    return cmd


def _tail(path, limit=400):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-3:])[-limit:]


def counts_of(items):
    out = {st: 0 for st in ("ok", "skipped", "failed", "interrupted", "pending")}
    for item in items:
        st = item.get("status")
        if st in out:
            out[st] += 1
    return out


def _drop_if_empty(path):
    """失败后留下的空目录（子进程刚 makedirs 就挂了）顺手清掉；有文件就留着给人看。"""
    try:
        os.rmdir(path)
    except OSError:
        pass


def _batch_summary(out_dir, urls, items):
    """counts 一律从 items 重算：增量写盘、中断、pending 都不会和明细对不上。"""
    return {"schema": 1, "vsum_version": __version__, "out": out_dir,
            "total": len(urls), "counts": counts_of(items), "items": items}


def _pin_device(dev):
    """串行时也能按 --devices 选卡：临时改环境变量，退出时还原。"""
    keys = ("CUDA_VISIBLE_DEVICES", "WHISPER_DEVICE")
    saved = {k: os.environ.get(k) for k in keys}
    if dev.startswith("cuda:"):
        os.environ["CUDA_VISIBLE_DEVICES"] = dev.split(":", 1)[1]
        os.environ["WHISPER_DEVICE"] = "cuda"
    else:
        os.environ["WHISPER_DEVICE"] = dev
    return saved


def _unpin_device(saved):
    for key, old in (saved or {}).items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _run_parallel(urls, out_dir, model, language, pack, browser, headless, profile,
                  force, context, jobs, devices, decode_opts):
    """多子进程：CUDA context 和模型缓存都是进程级的，fork 会炸，Windows 也没 fork。"""
    batch_path = os.path.join(out_dir, "batch.json")
    log_dir = os.path.join(out_dir, ".logs")
    items, queue = [], []
    for idx, url in enumerate(urls):
        name = slug(url)
        target = os.path.join(out_dir, name)
        if not force and _ok_result(os.path.join(target, "result.json")):
            items.append({"url": url, "dir": name, "status": "skipped"})
        else:
            items.append({"url": url, "dir": name, "status": "pending"})
            queue.append(idx)
    if not queue:
        _atomic_write(_batch_summary(out_dir, urls, items), batch_path)
        return 0
    devs = _device_list(devices)
    if len(devs) < jobs:
        if not devices:
            cards = len(hardware.probe())
            if cards:
                print("! 并行 %d 条但没指定 --devices：探测到 %d 张卡，子进程都默认用第 0 张"
                      "（分卡要 --devices cuda:0,cuda:1）" % (jobs, cards))
            else:
                print("! 并行 %d 条但没指定 --devices，也没探测到 CUDA 卡："
                      "并行只对下载/解码这类等待有用" % jobs)
        else:
            print("! 并行 %d 条但 --devices 只给了 %d 个：多出来的进程会共享同一张卡"
                  % (jobs, len(devs)))
    running = {}
    try:
        while queue or running:
            while queue and len(running) < jobs:
                idx = queue.pop(0)
                url = urls[idx]
                target = os.path.join(out_dir, items[idx]["dir"])
                env = dict(os.environ)
                dev = devs[len(running) % len(devs)] if devs else None
                if dev:
                    if dev.startswith("cuda:"):
                        env["CUDA_VISIBLE_DEVICES"] = dev.split(":", 1)[1]
                        env["WHISPER_DEVICE"] = "cuda"
                    else:
                        env["WHISPER_DEVICE"] = dev
                # 日志放 .logs/：目标目录由子进程自己建，失败时不该只留一个空目录和日志
                os.makedirs(log_dir, exist_ok=True)
                log_name = "%03d-%s.log" % (idx + 1, items[idx]["dir"])
                log_path = os.path.join(log_dir, log_name)
                fh = open(log_path, "w", encoding="utf-8")
                proc = subprocess.Popen(
                    _child_cmd(url, target, model, language, pack, browser, headless,
                               profile, force, context, decode_opts),
                    env=env, stdout=fh, stderr=subprocess.STDOUT)
                running[idx] = {"proc": proc, "fh": fh, "log": log_path,
                                "log_name": log_name, "t0": time.time()}
                print("[%d/%d] 启动 %s（pid %d%s）" % (
                    idx + 1, len(urls), items[idx]["dir"], proc.pid,
                    "，" + dev if dev else ""))
            time.sleep(0.2)
            for idx, run in list(running.items()):
                rc = run["proc"].poll()
                if rc is None:
                    continue
                run["fh"].close()
                del running[idx]
                item = items[idx]
                item["elapsed_sec"] = round(time.time() - run["t0"], 1)
                if rc == 0:
                    item["status"] = "ok"
                    try:
                        os.remove(run["log"])
                    except OSError:
                        pass
                else:
                    item["status"] = "failed"
                    _drop_if_empty(target)
                    item["error"] = "exit %d: %s（日志 .logs/%s）" % (
                        rc, _tail(run["log"]), run["log_name"])
                    print("  失败：%s" % item["error"])
                _atomic_write(_batch_summary(out_dir, urls, items), batch_path)
    except KeyboardInterrupt:
        for run in running.values():
            run["proc"].terminate()
            run["fh"].close()
        for idx in running:
            items[idx]["status"] = "interrupted"
        _atomic_write(_batch_summary(out_dir, urls, items), batch_path)
        raise
    return 5 if any(i.get("status") == "failed" for i in items) else 0


def _run_serial(urls, out_dir, model, language, pack, browser, headless, profile,
                force, context, decode_opts):
    batch_path = os.path.join(out_dir, "batch.json")
    items = []
    for i, url in enumerate(urls, 1):
        name = slug(url)
        target = os.path.join(out_dir, name)
        result_path = os.path.join(target, "result.json")
        print("[%d/%d] %s -> %s" % (i, len(urls), url, name))
        t0 = time.time()
        if not force and _ok_result(result_path):
            print("  跳过：已有 ok 的 result.json")
            items.append({"url": url, "dir": name, "status": "skipped"})
            continue
        try:
            pipeline.run(url, target, model, language, pack, browser=browser,
                         headless=headless, profile=profile, force=force, context=context,
                         hints=False, decode_opts=decode_opts)   # 批量跑时每条都打一遍「下一步」是噪音
            status, error = "ok", None
        except KeyboardInterrupt:
            items.append({"url": url, "dir": name, "status": "interrupted"})
            _atomic_write(_batch_summary(out_dir, urls, items), batch_path)
            raise
        except Exception as exc:
            status, error = "failed", "%s: %s" % (type(exc).__name__, exc)
            _drop_if_empty(target)
            print("  失败：%s" % error)
        items.append({"url": url, "dir": name, "status": status,
                      "error": error, "elapsed_sec": round(time.time() - t0, 1)})
        _atomic_write(_batch_summary(out_dir, urls, items), batch_path)
    # 末尾再写一次：最后几条若是「跳过」，循环里的写盘都不会触发。
    _atomic_write(_batch_summary(out_dir, urls, items), batch_path)
    print("批量完成：ok %d，skipped %d，failed %d -> %s"
          % (counts_of(items)["ok"], counts_of(items)["skipped"],
             counts_of(items)["failed"], batch_path))
    return 5 if any(i.get("status") == "failed" for i in items) else 0


def run(list_path, out_dir, model="small", language="zh", pack=None,
        browser=None, headless=None, profile=None, force=False, context=True,
        jobs=1, devices=None, decode_opts=None):
    urls = load_list(list_path)
    os.makedirs(out_dir, exist_ok=True)
    if jobs and jobs > 1:
        return _run_parallel(urls, out_dir, model, language, pack, browser, headless,
                             profile, force, context, jobs, devices, decode_opts)
    devs = _device_list(devices)
    if devs:
        if len(devs) > 1:
            print("! --jobs 1 只用第一个设备：%s（要多卡并行加 --jobs）" % devs[0])
        saved = _pin_device(devs[0])
        try:
            return _run_serial(urls, out_dir, model, language, pack, browser, headless,
                               profile, force, context, decode_opts)
        finally:
            _unpin_device(saved)
    if devices:
        print("! --devices 一个也没认出来，串行跑默认设备")
    return _run_serial(urls, out_dir, model, language, pack, browser, headless,
                       profile, force, context, decode_opts)

# -*- coding: utf-8 -*-
"""一条命令的主流程：媒体/链接 -> audio.wav -> 转写（可复用）-> 四件套 + 摘要 + result.json。"""
import json
import os
import shutil
import subprocess
import time

from . import (__version__, clean, export, manifest as manifest_mod, result as result_mod,
               summary as summary_mod, termrules, transcribe as tr)


class InputError(RuntimeError):
    """输入问题（媒体不存在、URL 未支持）。CLI 映射成退出码 2。"""


def ffmpeg_path():
    path = shutil.which("ffmpeg")
    if not path:
        raise tr.DependencyError("ffmpeg 未找到：macOS 用 brew install ffmpeg，"
                                 "Debian/Ubuntu 用 apt install ffmpeg，"
                                 "Windows 用 winget install Gyan.FFmpeg 或把 ffmpeg.exe 放进 PATH")
    return path


def probe_duration(path):
    """ffprobe 优先；没有就退到 PyAV（macOS 上装 ffmpeg 常常不带 ffprobe）。"""
    probe = shutil.which("ffprobe")
    if probe:
        try:
            out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                  "-of", "default=nk=1:nw=1", path],
                                 capture_output=True, text=True, timeout=30)
            return round(float(out.stdout.strip()), 2)
        except Exception:
            pass
    try:
        import av

        with av.open(path) as container:
            if container.duration is not None:
                return round(container.duration / 1e6, 2)
    except Exception:
        return None


def audio_reusable(src, wav, out_dir):
    """只有在 manifest 能证明「同一个输入、同一个音频」时才复用；否则重抽。"""
    man_path = os.path.join(out_dir, "manifest.json")
    if not os.path.exists(man_path):
        return False, "没有 manifest 可对账"
    try:
        man = manifest_mod.read(man_path)
        if man["input"]["sha256"] != manifest_mod.sha256_file(src):
            return False, "输入文件 sha256 变了"
        if man["audio"]["sha256"] != manifest_mod.sha256_file(wav):
            return False, "audio.wav 与 manifest 记录的 sha256 不符"
    except (OSError, ValueError, KeyError):
        return False, "manifest 读不了或不完整"
    return True, ""


def ensure_audio(src, out_dir):
    wav = os.path.join(out_dir, "audio.wav")
    if os.path.exists(wav):
        ok, why = audio_reusable(src, wav, out_dir)
        if ok:
            print("audio.wav 与输入一致（manifest sha256 对得上），复用")
            return wav
        print("audio.wav 不可复用（%s），重抽" % why)
        os.remove(wav)
    print("抽 16k 单声道音频")
    cmd = [ffmpeg_path(), "-y", "-v", "error", "-i", src, "-vn",
           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(wav):
        raise tr.DependencyError("ffmpeg 抽音频失败：%s" % (proc.stderr.strip()[:300] or "exit %d" % proc.returncode))
    return wav


def segments_reusable(out_dir, src, model, force=False):
    """转写复用条件（全部满足）：manifest 在、输入 sha 一致、segments 非空、model 相同、没给 --force。"""
    if force:
        return False, None, "指定了 --force"
    man_path = os.path.join(out_dir, "manifest.json")
    seg_path = os.path.join(out_dir, "segments.json")
    if not os.path.exists(man_path):
        return False, None, "没有 manifest"
    if not os.path.exists(seg_path):
        return False, None, "没有 segments.json"
    try:
        man = manifest_mod.read(man_path)
        if man["input"]["sha256"] != manifest_mod.sha256_file(src):
            return False, None, "输入文件 sha256 变了"
        if (man.get("decode") or {}).get("model") != model:
            return False, None, "上次用的模型不是 %s" % model
    except (OSError, ValueError, KeyError):
        return False, None, "manifest 读不了或不完整"
    try:
        with open(seg_path, encoding="utf-8") as fh:
            segments = json.load(fh).get("segments") or []
    except (OSError, ValueError):
        return False, None, "segments.json 读不了"
    if not segments:
        return False, None, "segments.json 是空的"
    return True, segments, ""


def load_corrections(out_dir):
    """corrections.json 有两种形状：vsum fix 写的是 files[].corrections，早期/手写的可能是顶层 corrections。都拍平成记录列表。"""
    path = os.path.join(out_dir, "corrections.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("files"), list):
        return [c for f in data["files"] if isinstance(f, dict) for c in (f.get("corrections") or [])]
    return data.get("corrections") or []


def run(src, out_dir, model="small", language="zh", pack=None,
        clean_mode=False, words=None, browser=None, fetch_only=False,
        headless=None, profile=None, summary_mode="extractive",
        llm_url=None, llm_model=None, force=False):
    t0 = time.time()
    source_url = None
    title = None
    fetch_warnings = []
    if src.startswith(("http://", "https://")):
        from . import fetch as fetch_mod
        os.makedirs(out_dir, exist_ok=True)
        info = fetch_mod.run(src, out_dir, browser=browser,
                             headless=headless, profile=profile)
        source_url = src
        title = info.get("title")
        fetch_warnings = list(info.get("warnings") or [])
        src = info.get("video") or os.path.join(out_dir, "video.mp4")
        if not os.path.exists(src):
            raise InputError("抓取完成但没找到视频文件：%s" % src)
        if fetch_only:
            print("只抓取：%s" % src)
            return {"fetched": src, "source_url": source_url}
    if not os.path.exists(src):
        raise InputError("找不到输入文件：%s" % src)
    os.makedirs(out_dir, exist_ok=True)

    reused, segments, why = segments_reusable(out_dir, src, model, force)
    man = None
    if reused:
        man = manifest_mod.read(os.path.join(out_dir, "manifest.json"))
        decode = man.get("decode") or {}
        duration = (man.get("input") or {}).get("duration")
        print("复用已有转写（%s 段；输入与模型都对得上），重跑导出与摘要" % len(segments))
    else:
        if why:
            print("重跑转写：%s" % why)
        wav = ensure_audio(src, out_dir)
        duration = probe_duration(wav)
        segments, info, decode = tr.transcribe_audio(wav, out_dir, model, language, pack, words)

    meta = {
        "title": title or os.path.splitext(os.path.basename(src))[0],
        "source": source_url or os.path.abspath(src),
        "duration_sec": duration,
        "model": model,
        "language": language,
        "terms_pack": os.path.basename(pack) if pack else None,
        "low_conf": export.low_conf_indexes(segments),
    }
    corrections = load_corrections(out_dir)
    clean_md = clean.clean_md(segments) if clean_mode else None
    outputs, export_warnings = export.export_all(
        segments, out_dir, clean_md, source=os.path.join(out_dir, "segments.json"),
        meta=meta, corrections=corrections)

    rules = termrules.load(pack)
    md_text, used_mode, bullets, dropped, summary_warnings = summary_mod.build(
        segments, meta, summary_mode, llm_url, llm_model, rules=rules)
    summary_path = os.path.join(out_dir, "summary.md")
    if md_text:
        with open(summary_path, "w", encoding="utf-8") as fh:
            fh.write(md_text)
        outputs["summary"] = summary_path
    elif os.path.exists(summary_path):
        os.remove(summary_path)

    warnings = list(fetch_warnings) + list(summary_warnings)
    if export_warnings["invalid_cues"]:
        warnings.append("导出时修正了 %d 条异常 cue（空文本/负时间/零时长）"
                        % len(export_warnings["invalid_cues"]))

    artifacts = {
        "transcript_md": "transcript.md",
        "summary_md": "summary.md" if md_text else None,
        "srt": "transcript.srt",
        "vtt": "transcript.vtt",
        "segments": "segments.json",
        "manifest": "manifest.json",
    }
    sha = {}
    for name in ("transcript.md", "summary.md", "segments.json"):
        path = os.path.join(out_dir, name)
        if os.path.exists(path):
            sha[name] = manifest_mod.sha256_file(path)
    counts = {
        "segments": len(segments),
        "chars": sum(len(s["text"]) for s in segments),
        "low_conf": len(meta["low_conf"]),
        "corrections_applied": sum(1 for r in corrections if r.get("applied")),
    }
    res = result_mod.build(True, out_dir,
                           {"kind": fetch_mod_kind(source_url) if source_url else "file",
                            "url": source_url, "input": os.path.basename(src)},
                           counts, reused,
                           {"mode": used_mode, "bullets": bullets, "dropped": dropped},
                           artifacts, sha, warnings, time.time() - t0, __version__)
    result_path = result_mod.write(res, out_dir)
    outputs["result"] = result_path

    man = manifest_mod.build(src, os.path.join(out_dir, "audio.wav"), decode, segments, outputs,
                             export_warnings, terms_path=pack, duration=duration,
                             mode="clean" if clean_mode else "verbatim",
                             version=__version__, source_url=source_url,
                             summary={"mode": used_mode, "bullets": bullets, "dropped": dropped})
    manifest_mod.write(man, os.path.join(out_dir, "manifest.json"))
    print("完成：%d 段，%d 字，摘要 %d 条 -> %s"
          % (len(segments), counts["chars"], bullets, os.path.abspath(out_dir)))
    return res


def fetch_mod_kind(url):
    from . import fetch as fetch_mod
    return fetch_mod.detect(url) or "other"
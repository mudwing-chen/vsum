# -*- coding: utf-8 -*-
"""一条命令的主流程：媒体/链接 -> audio.wav -> 转写（可复用）-> 四件套 + 摘要 + result.json。"""
import json
import os
import subprocess
import time

from . import (__version__, atomic, clean, export, manifest as manifest_mod,
               result as result_mod, summary as summary_mod, termrules, toolpath,
               transcribe as tr)


class InputError(RuntimeError):
    """输入问题（媒体不存在、URL 未支持）。CLI 映射成退出码 2。"""


def ffmpeg_path():
    path = toolpath.which("ffmpeg")
    if not path:
        raise tr.DependencyError("ffmpeg 未找到：macOS 用 brew install ffmpeg，"
                                 "Debian/Ubuntu 用 apt install ffmpeg，"
                                 "Windows 用 winget install Gyan.FFmpeg 或把 ffmpeg.exe 放进 PATH")
    return path


def probe_duration(path):
    """ffprobe 优先；wav 直接读头（零依赖）；最后退到 PyAV。

    macOS 上用 brew 装的 ffmpeg 常常不带 ffprobe，而 vsum 自己的 audio.wav 一定是
    ffmpeg 转出来的 16k 单声道 PCM——读它是纯 stdlib 的事，没必要因为缺 ffprobe
    就让 manifest 的时长永远是 null。
    """
    probe = toolpath.which("ffprobe")
    if probe:
        try:
            out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                  "-of", "default=nk=1:nw=1", path],
                                 capture_output=True, text=True, timeout=30)
            return round(float(out.stdout.strip()), 2)
        except Exception:
            pass
    if str(path).lower().endswith(".wav"):
        try:
            import wave

            with wave.open(path, "rb") as w:
                rate = w.getframerate()
                if rate:
                    return round(w.getnframes() / float(rate), 2)
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


def has_audio_stream(path):
    """有音频流返回 True，没有返回 False，探不出来返回 None（不阻塞主流程）。"""
    probe = toolpath.which("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run([probe, "-v", "error", "-select_streams", "a",
                              "-show_entries", "stream=index", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return bool(out.stdout.strip())


def ensure_audio(src, out_dir):
    wav = os.path.join(out_dir, "audio.wav")
    if os.path.exists(wav):
        ok, why = audio_reusable(src, wav, out_dir)
        if ok:
            print("audio.wav 与输入一致（manifest sha256 对得上），复用")
            return wav
        print("audio.wav 不可复用（%s），重抽" % why)
        os.remove(wav)
    # 纯画面无音轨的文件抽出来是空 wav，ffmpeg 只会报 "does not contain any stream"，
    # 那句话对着「转写失败」毫无帮助，先自己讲清楚。
    if has_audio_stream(src) is False:
        raise InputError("输入没有音频流（只有画面），没有东西可转写：%s" % os.path.basename(src))
    print("抽 16k 单声道音频")
    cmd = [ffmpeg_path(), "-y", "-v", "error", "-i", src, "-vn",
           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(wav):
        # 走到这里说明 ffmpeg 在（不在的话 ffmpeg_path() 已经报了依赖错误），
        # 失败原因是这个输入：损坏、或者根本不是音视频。算输入问题（2），不是缺依赖（3）。
        raise InputError("ffmpeg 抽不出音频：%s（%s）"
                         % (src, proc.stderr.strip()[:300] or "exit %d" % proc.returncode))
    return wav


def segments_reusable(out_dir, src, model, force=False, language=None, context=None,
                      words=None, pack=None, decode_opts=None):
    """转写复用条件：manifest 在、输入 sha 一致、segments 非空、**解码参数全一致**、没给 --force。

    只比模型名是不够的：换 language / condition_on_previous_text 都会改变输出。
    以前这里漏了这两项，于是 `--lang en` 会静默复用中文转写，再把 md 头写成
    `language: en` —— 产物自己和自己打架。宁可重跑，也不要作假。

    --words / 热词内容 / WHISPER_PROMPT 同理：带 --words 重跑却复用没有词级时间戳的
    旧稿，比多花十分钟更糟。这些由 transcribe.config_digest() 的指纹统一覆盖；
    0.6 之前的 manifest 没有指纹，就退回逐项比当时记下来的那几项。
    """
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
        decode = man.get("decode") or {}
        if man["input"]["sha256"] != manifest_mod.sha256_file(src):
            return False, None, "输入文件 sha256 变了"
        if decode.get("model") != model:
            return False, None, "上次用的模型是 %s，这次要 %s" % (decode.get("model"), model)
        if language is not None and decode.get("language") != language:
            return False, None, "上次的语言是 %s，这次要 %s" % (decode.get("language"), language)
        stored = decode.get("config_sha256")
        if stored:
            # 新 manifest 由指纹统一判：批推理下 manifest 记的 context 是 False（实际生效值），
            # 不能拿命令行的 True 去比，否则每次带 --batch-size 都判成「变了」。
            opts = decode_opts or {}
            if stored != tr.config_digest(
                    language, terms_path=pack, words=words, context=context,
                    batch_size=opts.get("batch_size", 1),
                    vad_options=opts.get("vad_options"),
                    chunk_length=opts.get("chunk_length")):
                return False, None, "解码参数变了（--words / 热词 / 批大小 / VAD / chunk 等）"
        else:
            # 0.6 之前的 manifest 没有指纹，退回逐项比当时记下来的那几项。
            if context is not None and bool(decode.get("condition_on_previous_text")) != bool(context):
                return False, None, "上次 context=%s，这次要 %s" % (
                    bool(decode.get("condition_on_previous_text")), bool(context))
            if words is not None and bool(decode.get("word_timestamps")) != bool(words):
                return False, None, "上次 word_timestamps=%s，这次要 %s" % (
                    bool(decode.get("word_timestamps")), bool(words))
            prompt = tr.default_prompt(language)
            if decode.get("initial_prompt") not in (None, prompt):
                return False, None, "WHISPER_PROMPT 变了"
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
        llm_url=None, llm_model=None, llm_key=None, force=False, context=True,
        hints=True, decode_opts=None):
    t0 = time.time()
    # --model auto 必须在复用判断之前解析：manifest 里记的是具体模型名。
    model = tr.resolve_model(model)
    opts = tr.decode_options(decode_opts)
    # CPU 上批推理没有意义，先折成 1 再算指纹；否则 --batch-size 4 与 1 会算出
    # 两个不同的 config_sha256，下次换回默认值就白重转一遍。
    if opts.get("batch_size") != 1:
        hw = tr.hardware.resolve()
        opts["batch_size"] = tr.resolve_batch_size(opts["batch_size"], hw["device"], hw)
    source_url = None
    title = None
    fetch_warnings = []
    if src.startswith(("http://", "https://")):
        from . import fetch as fetch_mod
        # 目录留给 fetch 自己建：它要先认链接类型，不认识的链接不该留下空目录
        info = fetch_mod.run(src, out_dir, browser=browser,
                             headless=headless, profile=profile, force=force)
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
    stale = os.path.join(out_dir, "result.json")
    if os.path.exists(stale):
        os.remove(stale)

    reused, segments, why = segments_reusable(out_dir, src, model, force, language, context,
                                              words=words, pack=pack, decode_opts=opts)
    if reused:
        old = manifest_mod.read(os.path.join(out_dir, "manifest.json"))
        decode = old.get("decode") or {}
        duration = (old.get("input") or {}).get("duration")
        print("复用已有转写（%s 段；输入、模型、解码参数都对得上），重跑导出与摘要" % len(segments))
        # 复用不需要重听，但这一轮的 manifest 要记 audio.wav 的 sha256：用户为省空间
        # 删掉 audio.wav 后重跑，不能等到最后一步才 FileNotFoundError。缺了就重抽一份。
        wav = os.path.join(out_dir, "audio.wav")
        if not os.path.exists(wav):
            print("audio.wav 不在（复用转写用不到它，但 manifest 要记它的 sha256），重抽音频")
            wav = ensure_audio(src, out_dir)
        if duration is None:
            # 上次没探出时长（当时既没有 ffprobe 也没有 PyAV），这时候音频已经在手边
            # 或者刚重抽过，补探一次；否则 md 头的 duration_sec 会一直是 null。
            duration = probe_duration(wav)
    else:
        if why:
            print("重跑转写：%s" % why)
        wav = ensure_audio(src, out_dir)
        duration = probe_duration(wav)
        segments, info, decode = tr.transcribe_audio(
            wav, out_dir, model, language, pack, words, context=context,
            batch_size=opts["batch_size"], vad_options=opts["vad_options"],
            chunk_length=opts["chunk_length"])

    # 标签一律取自「实际跑的参数」，不是命令行请求的参数：复用路径下两者可能不同，
    # 用命令行参数去写 md 头就会出现「md 说 en、正文是中文、manifest 说 zh」。
    meta = {
        "title": title or os.path.splitext(os.path.basename(src))[0],
        "source": source_url or os.path.abspath(src),
        "duration_sec": duration,
        "model": decode.get("model", model),
        # --lang auto 时 decode 里同时记了请求值和检测值，头里写检测值才有信息量
        "language": decode.get("detected_language") or decode.get("language", language),
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
        segments, meta, summary_mode, llm_url, llm_model, rules=rules, llm_key=llm_key)
    summary_path = os.path.join(out_dir, "summary.md")
    if md_text:
        atomic.write_text(summary_path, md_text)
        outputs["summary"] = summary_path
    elif os.path.exists(summary_path):
        os.remove(summary_path)

    decode_warnings = []
    # 复用路径的 decode 来自旧 manifest：上一轮的降档/回退警告不能当成这一轮发生的事
    # 再报一遍，否则 result.json 会说「已退回串行」而这次根本没跑模型。
    if not reused:
        if decode.get("batch_fallback"):
            decode_warnings.append("批推理不可用，已退回串行：%s" % decode["batch_fallback"])
        for fb in decode.get("fallbacks") or []:
            decode_warnings.append("转写降档（%s）：%s → %s" % (
                fb.get("reason"), fb.get("from"), fb.get("to")))
    hf_endpoint = decode.get("hf_endpoint")
    if hf_endpoint and "huggingface.co" not in hf_endpoint:
        warnings.append("模型来自镜像 %s（vsum 不校验权重摘要），需要严格复现时请固定模型目录"
                        % hf_endpoint)
    warnings = list(fetch_warnings) + decode_warnings + list(summary_warnings)
    if export_warnings["invalid_cues"]:
        warnings.append("导出时修正了 %d 条异常 cue（空文本/负时间/零时长）"
                        % len(export_warnings["invalid_cues"]))
    if export_warnings["long_cues"]:
        warnings.append("有 %d 条 cue 超过 %.0f 秒，字幕可读性差"
                        % (len(export_warnings["long_cues"]), export.LONG_SEC))

    artifacts = {
        "transcript_md": "transcript.md",
        "plain": "transcript.plain.txt",
        "srt": "transcript.srt",
        "vtt": "transcript.vtt",
        "clean_md": os.path.basename(outputs["clean_md"]) if outputs.get("clean_md") else None,
        "summary_md": "summary.md" if md_text else None,
        "segments": "segments.json",
        "manifest": "manifest.json",
    }
    counts = {
        "segments": len(segments),
        "chars": sum(len(s["text"]) for s in segments),
        "low_conf": len(meta["low_conf"]),
        "corrections_applied": sum(1 for r in corrections if r.get("applied")),
    }
    # overview_verified 只对抽取式为真：LLM 的「一句话」是模型改写，回查门只约束要点
    summary_info = {"mode": used_mode, "bullets": bullets, "dropped": dropped,
                    "overview_verified": used_mode != "llm"}
    # manifest 先写、result.json 最后写：result.json 里的 ok 是「这一轮跑完了」的唯一
    # 凭据（batch 靠它跳过），它必须最后一个落地，中途失败时目录里不能有半新半旧的组合。
    man = manifest_mod.build(src, wav, decode, segments, outputs, warnings, terms_path=pack,
                             duration=duration, mode="clean" if clean_mode else "verbatim",
                             version=__version__, source_url=source_url,
                             summary=summary_info,
                             export=export_warnings)
    manifest_mod.write(man, os.path.join(out_dir, "manifest.json"))

    # sha256 覆盖这一轮实际写出的每个产物。manifest/result 不含在内：它们没法记录自己的哈希。
    sha = {}
    for path in list(outputs.values()) + [os.path.join(out_dir, "segments.json")]:
        if path and os.path.exists(path):
            sha[os.path.basename(path)] = manifest_mod.sha256_file(path)

    res = result_mod.build(True, out_dir,
                           {"kind": fetch_mod_kind(source_url) if source_url else "file",
                            "url": source_url, "input": os.path.basename(src)},
                           counts, reused,
                           summary_info,
                           artifacts, sha, warnings, time.time() - t0, __version__)
    result_path = result_mod.write(res, out_dir)
    outputs["result"] = result_path
    print("完成：%d 段，%d 字，摘要 %d 条 -> %s"
          % (len(segments), counts["chars"], bullets, os.path.abspath(out_dir)))
    if hints:
        # 新人的下一步几乎总是「那句在第几秒」和「把那一段剪出来」，
        # 直接给可复制的命令，别让他回头翻 README。
        print("  想知道某句在第几秒：vsum find %s 关键词" % out_dir)
        print("  想把那一段剪出来：  vsum clip %s 00:41 -o clip.mp4" % out_dir)
    return res


def fetch_mod_kind(url):
    from . import fetch as fetch_mod
    return fetch_mod.detect(url) or "other"
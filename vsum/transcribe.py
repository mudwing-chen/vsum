# -*- coding: utf-8 -*-
"""faster-whisper 转写。模型是懒加载的：不跑转写就不会 import faster_whisper。"""
import json
import os
import time

from . import hardware, termrules

DEFAULT_PROMPT = "以下是普通话视频内容。"
# 显式写死，进 manifest；温度回退会影响可复现性，但关掉它长音频更容易卡在复读上
TEMPERATURE = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


class DependencyError(RuntimeError):
    """缺 ffmpeg / faster-whisper / 模型。CLI 映射成退出码 3。"""


class TranscribeError(RuntimeError):
    """解码/转写失败（空音频、模型崩）。CLI 映射成退出码 4。"""


def av_incompatible(exc):
    """faster-whisper 1.2.1 + av>=14 的已知崩法：metadata_errors 参数被删。"""
    return isinstance(exc, TypeError) and "metadata_errors" in str(exc)


def pick_device():
    """有 CUDA 才用 cuda；WHISPER_DEVICE 仍可覆盖。"""
    want = os.environ.get("WHISPER_DEVICE", "").strip()
    if want:
        return want
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def load_hotwords(terms_path=None):
    """WHISPER_HOTWORDS=1 时把词表的 canonical 拼成提示词；默认关。"""
    if os.environ.get("WHISPER_HOTWORDS", "0").strip() in ("", "0"):
        return None
    try:
        with open(terms_path or termrules.DEFAULT_TERMS, encoding="utf-8") as fh:
            words = [t["canonical"] for t in json.load(fh)["terms"]]
    except Exception:
        return None
    return " ".join(words) or None


def _words_enabled():
    return os.environ.get("WHISPER_WORDS", "0").strip() not in ("", "0")


def segment_record(s, words=False):
    """单段 → segments.json 的记录。temperature 用来解释温度回退带来的文本差异。"""
    rec = {
        "start": round(float(s.start), 2), "end": round(float(s.end), 2), "text": s.text.strip(),
        "avg_logprob": None if s.avg_logprob is None else round(float(s.avg_logprob), 4),
        "no_speech_prob": None if s.no_speech_prob is None else round(float(s.no_speech_prob), 4),
        "compression_ratio": None if s.compression_ratio is None else round(float(s.compression_ratio), 3),
        "temperature": None if getattr(s, "temperature", None) is None else round(float(s.temperature), 3),
    }
    if words:
        rec["words"] = [
            {"start": round(float(w.start), 2), "end": round(float(w.end), 2),
             "word": w.word,
             "probability": None if w.probability is None else round(float(w.probability), 3)}
            for w in (s.words or [])
        ]
    return rec


def fallback_count(segments):
    """温度回退过的段数。0 是「两次跑文本一致」的必要条件（不是充分条件）。"""
    return sum(1 for s in segments if s.get("temperature"))


def decode_args(model_name, language, terms_path=None, words=None):
    """返回 (构造参数, 记录进 manifest 的参数字典)。"""
    if words is None:
        words = _words_enabled()
    hw = hardware.resolve()
    device = hw["device"]
    compute = hw["compute_type"]
    prompt = os.environ.get("WHISPER_PROMPT", DEFAULT_PROMPT)
    hot = load_hotwords(terms_path)
    kwargs = dict(language=language, vad_filter=True, beam_size=5,
                  condition_on_previous_text=False,
                  temperature=TEMPERATURE, initial_prompt=prompt)
    if hot:
        kwargs["hotwords"] = hot
    if words:
        kwargs["word_timestamps"] = True
    recorded = {
        "model": model_name, "device": device, "compute_type": compute,
        "language": language, "beam_size": 5, "vad_filter": True,
        "condition_on_previous_text": False, "temperature": list(TEMPERATURE),
        "initial_prompt": prompt, "hotwords": bool(hot), "word_timestamps": bool(words),
        "device_source": hw["source"], "gpu": hw["name"], "model_cap": hw["model_cap"],
    }
    return kwargs, recorded, device, compute


def transcribe_audio(audio_path, out_dir, model_name="medium", language="zh",
                     terms_path=None, words=None, on_progress=None):
    """跑一次转写，写 transcript.txt + segments.json，返回 (segments, info, decode 记录)。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise DependencyError("faster-whisper 未安装：pip install faster-whisper（%s）" % exc)

    kwargs, recorded, device, compute = decode_args(model_name, language, terms_path, words)
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute)
    except Exception as exc:
        raise DependencyError("模型加载失败（%s / %s / %s）：%s" % (model_name, device, compute, exc))
    started = time.time()
    try:
        segs, info = model.transcribe(audio_path, **kwargs)
    except Exception as exc:
        if av_incompatible(exc):
            raise DependencyError("av 版本过新：faster-whisper 1.2.1 与 av>=14 不兼容，"
                                  "装 av<14（pip install 'av<14'）") from exc
        raise TranscribeError("转写失败：%s" % exc) from exc

    lines, segments = [], []
    last_report = 0.0
    for s in segs:
        t = s.text.strip()
        if t:
            lines.append("[%6.1f -> %6.1f] %s" % (s.start, s.end, t))
            segments.append(segment_record(s, recorded["word_timestamps"]))
        now = time.time()
        if now - last_report > 15:
            last_report = now
            if on_progress:
                on_progress(len(segments), s.end, now - started)
            else:
                print("PROGRESS segments=%d at=%.1fs elapsed=%.0fs" % (len(segments), s.end, now - started),
                      flush=True)

    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(out_dir, "segments.json"), "w", encoding="utf-8") as fh:
        json.dump({"segments": segments}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    recorded["fallback_segments"] = fallback_count(segments)
    low = sorted([s for s in segments if s["avg_logprob"] is not None],
                 key=lambda s: s["avg_logprob"])[:10]
    if low:
        print("LOW_CONF n=%d times=%s" % (len(low), ",".join("%.1f" % s["start"] for s in low)), flush=True)
    print("OK_SEGMENTS=%d CHARS=%d" % (len(lines), sum(len(s["text"]) for s in segments)))
    print("LANG=%s DUR=%.1f ELAPSED=%.1fs" % (info.language, info.duration, time.time() - started))
    return segments, info, recorded

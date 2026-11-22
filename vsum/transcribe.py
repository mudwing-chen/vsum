# -*- coding: utf-8 -*-
"""faster-whisper 转写。模型是懒加载的：不跑转写就不会 import faster_whisper。

音频不走 av 解码：vsum 自己已经用 ffmpeg 抽了 16k 单声道 wav，这里直接用标准库
wave 读成 float32 数组喂给模型。这样 av 的版本变化（decode_audio 的 kvarg、
重采样行为）就完全影响不到转写结果——faster-whisper 只是把它当传递依赖装着。
"""
import json
import os
import time
import wave

try:  # numpy 是 faster-whisper 的依赖，正常装包一定在；CI 用 --no-deps 装包时可能没有
    import numpy as np
except ImportError:  # pragma: no cover - 只在无依赖的测试环境发生
    np = None

from . import hardware, termrules, textnorm

DEFAULT_PROMPT = "以下是普通话视频内容。"
# 显式写死，进 manifest；温度回退会影响可复现性，但关掉它长音频更容易卡在复读上
TEMPERATURE = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
SAMPLE_RATE = 16000

_MODELS = {}


class DependencyError(RuntimeError):
    """缺 ffmpeg / faster-whisper / 模型。CLI 映射成退出码 3。"""


class TranscribeError(RuntimeError):
    """解码/转写失败（空音频、模型崩）。CLI 映射成退出码 4。"""


def av_incompatible(exc):
    """faster-whisper 旧版本 + av>=14 的已知崩法：metadata_errors 参数被删。

    vsum 现在不把路径交给 faster-whisper（见 load_audio），所以这条只作为
    用户手工调用 decode_audio 时的诊断提示保留。
    """
    return isinstance(exc, TypeError) and "metadata_errors" in str(exc)


def load_audio(path):
    """16k 单声道 PCM wav -> float32 [-1,1] 数组；读不了返回 None（退回到传路径）。"""
    if np is None:  # 没装 numpy：交给 faster-whisper 自己解码（它一定带 numpy）
        return None
    try:
        with wave.open(path, "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            raw = w.readframes(w.getnframes())
            rate = w.getframerate()
    except (wave.Error, OSError, EOFError):
        return None
    if not raw:
        return None
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if rate != SAMPLE_RATE:
        return None  # 非 16k 交给 faster-whisper 自己重采样，别在这里做半套
    return audio


def to_simplified(text):
    """繁转简，返回 (文本, 是否可用)。规则见 vsum/textnorm.py；不影响时间戳。"""
    return textnorm.to_simplified(text), True


def ensure_cuda_libs():
    """把 site-packages/nvidia/*/bin 加进进程的 DLL 搜索路径。

    `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 会把 dll 放在**包内的 bin/**，
    Windows 默认不去那儿找，于是 ctranslate2 报 `cublas64_12.dll is not found`——
    明明装了、明明有卡，就是跑不起来。这一步把那些目录挂上去。
    """
    roots = set()
    try:
        import site
        import sysconfig

        for key in ("purelib", "platlib"):
            path = sysconfig.get_paths().get(key)
            if path:
                roots.add(path)
        roots.update(site.getsitepackages() if hasattr(site, "getsitepackages") else [])
    except Exception:
        pass
    if np is not None:  # 顺便把当前解释器自己的 site-packages 也算进来
        roots.add(os.path.dirname(os.path.dirname(np.__file__)))
    added = []
    for root in sorted(r for r in roots if r):
        nv = os.path.join(root, "nvidia")
        if not os.path.isdir(nv):
            continue
        for sub in sorted(os.listdir(nv)):
            bindir = os.path.join(nv, sub, "bin")
            if not os.path.isdir(bindir):
                continue
            if bindir not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
                added.append(bindir)
            try:
                os.add_dll_directory(bindir)
            except (AttributeError, OSError):
                pass
    return added


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


def decode_args(model_name, language, terms_path=None, words=None, context=True):
    """返回 (构造参数, 记录进 manifest 的参数字典)。

    context = condition_on_previous_text：中文实测差别很大。True 会沿用上文，
    输出简体、带标点、句子完整；False 每段当全新的听，会漂成繁体、丢标点，
    连摘要都没法按标点切句。默认 True，要老行为用 --no-context。
    """
    if words is None:
        words = _words_enabled()
    hw = hardware.resolve()
    device = hw["device"]
    compute = hw["compute_type"]
    prompt = os.environ.get("WHISPER_PROMPT", DEFAULT_PROMPT)
    hot = load_hotwords(terms_path)
    # 开上下文后切句更细、逗号更多，摘要才挑得出整句；WHISPER_COND=0 可全局退回。
    cond = bool(context) and os.environ.get("WHISPER_COND", "1").strip() not in ("", "0")
    kwargs = dict(language=language, vad_filter=True, beam_size=5,
                  condition_on_previous_text=cond,
                  temperature=TEMPERATURE, initial_prompt=prompt)
    if hot:
        kwargs["hotwords"] = hot
    if words:
        kwargs["word_timestamps"] = True
    recorded = {
        "model": model_name, "device": device, "compute_type": compute,
        "language": language, "beam_size": 5, "vad_filter": True,
        "condition_on_previous_text": cond, "temperature": list(TEMPERATURE), "t2s": True,
        "initial_prompt": prompt, "hotwords": bool(hot), "word_timestamps": bool(words),
        "device_source": hw["source"], "gpu": hw["name"], "model_cap": hw["model_cap"],
        "audio_input": "ndarray",
    }
    return kwargs, recorded, device, compute


def get_model(model_name, device, compute, cache=True):
    """模型缓存：同一进程里反复用同一个模型时不要重复加载。

    批量跑 N 条视频，每条重新 WhisperModel(...) 就要重付一次 CUDA 初始化
    （本机实测 20~40 秒）。batch 走的就是同一条进程，所以这里必须缓存。
    """
    key = (model_name, device, compute)
    if cache and key in _MODELS:
        return _MODELS[key], True
    from faster_whisper import WhisperModel

    if device == "cuda":
        ensure_cuda_libs()
    model = WhisperModel(model_name, device=device, compute_type=compute)
    if cache:
        _MODELS[key] = model
    return model, False


CUDA_HINTS = ("cublas", "cudnn", "cuda", "no kernel image", "device is detected")


def _looks_like_cuda_problem(exc):
    text = str(exc).lower()
    return any(h in text for h in CUDA_HINTS)


def transcribe_audio(audio_path, out_dir, model_name="small", language="zh",
                     terms_path=None, words=None, on_progress=None, context=True):
    """跑一次转写，写 transcript.txt + segments.json，返回 (segments, info, decode 记录)。"""
    kwargs, recorded, device, compute = decode_args(model_name, language, terms_path, words, context)
    try:
        model, cached = get_model(model_name, device, compute)
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise DependencyError("faster-whisper 未安装：pip install faster-whisper（%s）" % exc)
    except Exception as exc:
        if device == "cuda" and _looks_like_cuda_problem(exc):
            # 有卡但运行库不全：别把烂摊子丢给用户，退回 CPU 并说清楚
            print("! CUDA 起不来（%s），退回 CPU" % str(exc)[:120])
            print("! 想让 GPU 生效：pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
            device, compute = "cpu", "int8"
            recorded["device"] = device
            recorded["compute_type"] = compute
            recorded["device_source"] = "fallback"
            recorded["cuda_error"] = str(exc)[:200]
            kwargs, _, _, _ = decode_args(model_name, language, terms_path, words, context)
            kwargs["condition_on_previous_text"] = recorded["condition_on_previous_text"]
            model, cached = get_model(model_name, device, compute)
        else:
            raise DependencyError("模型加载失败（%s / %s / %s）：%s" % (model_name, device, compute, exc))
    if cached:
        print("模型已在内存里（%s / %s），跳过加载" % (model_name, device))
    started = time.time()
    audio = load_audio(audio_path)
    payload = audio if audio is not None else audio_path
    try:
        segs, info = model.transcribe(payload, **kwargs)
    except Exception as exc:
        if av_incompatible(exc):
            raise DependencyError("av 版本过新：faster-whisper 的 decode_audio 与 av>=14 不兼容，"
                                  "而 vsum 本不该走到解码这条路（传的是 wav 数组）。"
                                  "请确认输入是 16k 单声道 wav，或 pip install 'av<14'") from exc
        raise TranscribeError("转写失败：%s" % exc) from exc

    lines, segments = [], []
    last_report = 0.0
    t2s_ok = False
    for s in segs:
        t = s.text.strip()
        if t:
            # Whisper 没有正字法约束：实测 13:47 的稿子里 2.5% 的字落成繁体。
            # 这里只转字，不动时间戳与标点。
            t, _ok = to_simplified(t)
            t2s_ok = True
            lines.append("[%6.1f -> %6.1f] %s" % (s.start, s.end, t))
            rec = segment_record(s, recorded["word_timestamps"])
            rec["text"] = t
            segments.append(rec)
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

    recorded["t2s"] = t2s_ok
    recorded["fallback_segments"] = fallback_count(segments)
    low = sorted([s for s in segments if s["avg_logprob"] is not None],
                 key=lambda s: s["avg_logprob"])[:10]
    if low:
        print("LOW_CONF n=%d times=%s" % (len(low), ",".join("%.1f" % s["start"] for s in low)), flush=True)
    print("OK_SEGMENTS=%d CHARS=%d" % (len(lines), sum(len(s["text"]) for s in segments)))
    print("LANG=%s DUR=%.1f ELAPSED=%.1fs" % (info.language, info.duration, time.time() - started))
    return segments, info, recorded

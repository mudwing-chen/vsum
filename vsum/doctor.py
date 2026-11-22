# -*- coding: utf-8 -*-
"""vsum doctor：逐项检查依赖，缺的给安装命令。浏览器只报可用性，不检查登录态。"""
import importlib.util
import os
import platform
import shutil
import subprocess
import sys


def ffmpeg_hint(system=None):
    """各平台的 ffmpeg 安装命令；Windows 不再给 apt 建议。"""
    system = sys.platform if system is None else system
    if system.startswith("darwin"):
        return "brew install ffmpeg"
    if system in ("win32", "cygwin", "msys"):
        return "winget install Gyan.FFmpeg（或把 ffmpeg.exe 放进 PATH）"
    return "apt install ffmpeg"


def _first_line(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0]
    except Exception as exc:
        return "error: %s" % exc


def _model_cached(model):
    roots = [os.environ.get("HF_HUB_CACHE"),
             os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub"),
             os.path.expanduser("~/.cache/huggingface/hub")]
    name = "models--Systran--faster-whisper-" + model
    for root in roots:
        if root and os.path.isdir(os.path.join(root, name)):
            return os.path.join(root, name)
    return None


def run(model="small"):
    rows, ok = [], True

    def add(name, good, detail, hint=""):
        nonlocal ok
        if not good:
            ok = False
        rows.append((name, "ok" if good else "MISSING", detail, hint))

    add("python", sys.version_info >= (3, 9), "%d.%d.%d" % sys.version_info[:3],
        "需要 3.9+")

    ffmpeg = shutil.which("ffmpeg")
    add("ffmpeg", bool(ffmpeg), ffmpeg or "not found",
        ffmpeg_hint())
    if ffmpeg:
        rows[-1] = ("ffmpeg", "ok", "%s  %s" % (ffmpeg, _first_line([ffmpeg, "-version"])), "")

    ffprobe = shutil.which("ffprobe")
    add("ffprobe", bool(ffprobe), ffprobe or "not found", "随 ffmpeg 一起安装")

    spec = importlib.util.find_spec("faster_whisper")
    version = ""
    if spec:
        try:
            from importlib.metadata import version as v

            version = v("faster-whisper")
        except Exception:
            version = "installed"
    add("faster-whisper", bool(spec), version or "not found", "pip install faster-whisper")

    try:
        from importlib.metadata import version as _v

        av_ver = _v("av")
        major = int(av_ver.split(".")[0])
        add("av", major < 14, av_ver,
            "av>=14 与 faster-whisper 1.2.1 不兼容：pip install 'av<14'")
    except Exception:
        add("av", False, "not found", "pip install 'av>=11,<14'")

    from . import hardware

    hw = hardware.resolve()
    add("device", True, "%s / %s" % (hw["device"], hw["compute_type"]),
        "WHISPER_DEVICE / WHISPER_COMPUTE 可覆盖；实测 RTF 用 vsum bench")
    add("model_cap", True, hw["model_cap"], "按显存推的建议上限，不强制；真的快慢看 vsum bench")
    if hw["name"]:
        add("gpu", True, "%s %.1fGB cc%.1f" % (hw["name"], hw["vram_gb"], hw["cc"]), hw["reason"])

    from . import browser as _browser

    chrome = _browser.find_binary("chrome")
    edge = _browser.find_binary("edge")
    has_sel = importlib.util.find_spec("selenium") is not None
    detail = "selenium=%s chrome=%s edge=%s" % (
        "yes" if has_sel else "no",
        "yes" if chrome else "no",
        "yes" if edge else "no")
    if has_sel and (chrome or edge):
        rows.append(("browser", "ok", detail, ""))
    else:
        rows.append(("browser", "extra", detail,
                     "链接抓取不可用（本地文件不受影响）：pip install 'vsum[browser]' 并装 Chrome 或 Edge"))

    cached = _model_cached(model)
    add("model:%s" % model, bool(cached), cached or "未缓存",
        "首次运行会自动下载（约 1.5 GB），或先跑一次 vsum 让它下载")

    free = shutil.disk_usage(os.getcwd()).free / (1 << 30)
    add("disk", free > 2, "%.1f GB free" % free, "reserve >2 GB for audio + model")

    for name, state, detail, hint in rows:
        line = "%-14s %-8s %-46s" % (name, state, detail)
        if hint and state != "ok":
            line += "  " + hint
        print(line.rstrip())
    if sys.platform == "darwin" and platform.machine() == "arm64" and sys.version_info < (3, 10):
        print("note          macOS arm64 + Python 3.9 只能配旧版依赖："
              "onnxruntime<=1.16.3 / av<=13.1.0 / tokenizers<=0.21.4。"
              "上表 python 显示 ok 不代表 pip install faster-whisper 能装成；"
              "稳妥做法是 uv venv --python 3.12 再装。")
    return 0 if ok else 3
